"""
Game server for multiplayer tank game.
Handles client connections, lobby management, and game state synchronization.
"""

import socket
import threading
import json
import time
from typing import Dict
from protocol import MessageType, encode_message, decode_message


class GameServer:
    def __init__(self, port: int = 5001):
        self.port = port
        self.host = '0.0.0.0'
        self.server_socket = None
        self.running = False

        # Player management
        self.clients = {}  # client_id -> socket
        self.players = {}  # client_id -> player_data
        self.next_client_id = 1

        # Lobby state
        self.ready_players = set()
        self.game_started = False

        # Available colors for players
        self.available_colors = [
            (255, 0, 0),      # Red
            (0, 255, 0),      # Green
            (0, 0, 255),      # Blue
            (255, 255, 0),    # Yellow
            (255, 0, 255),    # Magenta
            (0, 255, 255),    # Cyan
            (255, 128, 0),    # Orange
            (128, 0, 255),    # Purple
            (0, 255, 128),    # Spring Green
            (255, 192, 203),  # Pink
        ]
        self.used_colors = []

        # Game state
        self.game_state = {
            "players": {},
            "bullets": []
        }

        self.lock = threading.Lock()

    def start(self):
        """Start the server."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(10)
        self.running = True

        print(f"Server started on port {self.port}")
        print(f"Players can connect using this address")

        # Start accepting connections
        accept_thread = threading.Thread(target=self.accept_connections)
        accept_thread.daemon = True
        accept_thread.start()

        # Start game loop
        game_thread = threading.Thread(target=self.game_loop)
        game_thread.daemon = True
        game_thread.start()

    def accept_connections(self):
        """Accept incoming client connections."""
        while self.running:
            try:
                client_socket, address = self.server_socket.accept()
                print(f"New connection from {address}")

                # Handle client in a separate thread
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address)
                )
                client_thread.daemon = True
                client_thread.start()
            except Exception as e:
                if self.running:
                    print(f"Error accepting connection: {e}")

    def get_next_color(self):
        """Get the next available unique color."""
        with self.lock:
            for color in self.available_colors:
                if color not in self.used_colors:
                    self.used_colors.append(color)
                    return color
            # If all colors used, return a random variation
            import random
            color = (random.randint(50, 255), random.randint(
                50, 255), random.randint(50, 255))
            self.used_colors.append(color)
            return color

    def release_color(self, color):
        """Release a color back to the pool."""
        with self.lock:
            if color in self.used_colors:
                self.used_colors.remove(color)

    def handle_client(self, client_socket, address):
        """Handle communication with a single client."""
        client_id = None
        buffer = b""
        print(f"Handling client {address}")

        try:
            # Assign client ID and color

            client_id = self.next_client_id
            self.next_client_id += 1
            self.clients[client_id] = client_socket
            color = self.get_next_color()
            self.players[client_id] = {
                "id": client_id,
                "color": color,
                "x": 400,
                "y": 300,
                "angle": 0,
                "health": 3,
                "alive": True
            }

            print(
                f"Assigned client ID {client_id} and color {color} to {address}")

            # Send connection accepted message
            msg = encode_message(MessageType.CONNECTION_ACCEPTED, {
                "client_id": client_id,
                "color": color
            })
            client_socket.send(msg)

            # Notify all clients about new player
            self.broadcast(MessageType.PLAYER_JOINED, {
                "player": self.players[client_id]
            })

            # Send current players list to new client
            with self.lock:
                for pid, pdata in self.players.items():
                    if pid != client_id:
                        msg = encode_message(MessageType.PLAYER_JOINED, {
                            "player": pdata
                        })
                        client_socket.send(msg)

            # Handle messages from client
            while self.running:
                data = client_socket.recv(4096)
                if not data:
                    break

                buffer += data

                # Process all complete messages in buffer
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        msg_type, msg_data = decode_message(line)
                        self.process_message(client_id, msg_type, msg_data)
                    except Exception as e:
                        print(f"Error processing message: {e}")

        except Exception as e:
            print(f"Client {client_id} error: {e}")
        finally:
            # Clean up
            self.remove_client(client_id)
            try:
                client_socket.close()
            except:
                pass

    def process_message(self, client_id, msg_type: MessageType, data: Dict):
        """Process a message from a client."""
        if msg_type == MessageType.READY:
            with self.lock:
                self.ready_players.add(client_id)
            self.broadcast(MessageType.PLAYER_READY, {
                "client_id": client_id
            })
            print(
                f"Player {client_id} is ready ({len(self.ready_players)}/{len(self.players)})")

        elif msg_type == MessageType.REQUEST_START:
            # Only the host (first connected player) can start
            if client_id == 1:
                if self.start_game():
                    print(f"Game started by host (player {client_id})")
                else:
                    print(
                        f"Start requested by player {client_id} but conditions not met")

        elif msg_type == MessageType.PLAYER_INPUT:
            # Update player state based on input
            with self.lock:
                if client_id in self.players:
                    player = self.players[client_id]
                    # Store the input for processing in game loop
                    player["input"] = data

    def remove_client(self, client_id):
        """Remove a client and notify others."""
        if client_id is None:
            return

        with self.lock:
            if client_id in self.clients:
                del self.clients[client_id]
            if client_id in self.players:
                color = self.players[client_id]["color"]
                self.release_color(color)
                del self.players[client_id]
            if client_id in self.ready_players:
                self.ready_players.remove(client_id)

        self.broadcast(MessageType.PLAYER_LEFT, {
            "client_id": client_id
        })
        print(f"Player {client_id} disconnected")

    def broadcast(self, msg_type: MessageType, data: Dict, exclude_client=None):
        """Broadcast a message to all clients."""
        msg = encode_message(msg_type, data)
        with self.lock:
            dead_clients = []
            for client_id, client_socket in self.clients.items():
                if client_id == exclude_client:
                    continue
                try:
                    client_socket.send(msg)
                except Exception as e:
                    print(f"Error sending to client {client_id}: {e}")
                    dead_clients.append(client_id)

            # Remove dead clients
            for client_id in dead_clients:
                self.remove_client(client_id)

    def game_loop(self):
        """Main game loop for updating game state."""
        dt = 1/60  # 60 FPS

        while self.running:
            time.sleep(dt)

            with self.lock:
                if not self.game_started:
                    # Check if we can start the game
                    if len(self.players) > 0 and len(self.ready_players) == len(self.players):
                        # All players ready, but only start if explicitly triggered
                        pass
                    continue

                # Process player inputs and update game state
                for client_id, player in self.players.items():
                    if not player.get("alive", True):
                        continue

                    player_input = player.get("input", {})

                    # Movement (holonomic)
                    speed = 200 * dt
                    if player_input.get("w"):
                        player["y"] -= speed
                    if player_input.get("s"):
                        player["y"] += speed
                    if player_input.get("a"):
                        player["x"] -= speed
                    if player_input.get("d"):
                        player["x"] += speed

                    # Rotation
                    rotation_speed = 180 * dt  # degrees per second
                    if player_input.get("left"):
                        player["angle"] -= rotation_speed
                    if player_input.get("right"):
                        player["angle"] += rotation_speed

                    # Shooting
                    if player_input.get("shoot") and not player.get("shot_cooldown", False):
                        import math
                        angle_rad = math.radians(player["angle"])
                        bullet = {
                            "owner_id": client_id,
                            "x": player["x"],
                            "y": player["y"],
                            "vx": math.cos(angle_rad) * 400,
                            "vy": math.sin(angle_rad) * 400,
                            "lifetime": 2.0
                        }
                        self.game_state["bullets"].append(bullet)
                        player["shot_cooldown"] = True
                        player["cooldown_timer"] = 0.5

                    # Update cooldown
                    if player.get("shot_cooldown"):
                        player["cooldown_timer"] = player.get(
                            "cooldown_timer", 0) - dt
                        if player["cooldown_timer"] <= 0:
                            player["shot_cooldown"] = False
                            player.pop("input", None)  # Clear shoot input
                            if "input" in player and "shoot" in player["input"]:
                                player["input"]["shoot"] = False

                    # Keep player in bounds
                    player["x"] = max(20, min(780, player["x"]))
                    player["y"] = max(20, min(580, player["y"]))

                # Update bullets
                bullets_to_remove = []
                for i, bullet in enumerate(self.game_state["bullets"]):
                    bullet["x"] += bullet["vx"] * dt
                    bullet["y"] += bullet["vy"] * dt
                    bullet["lifetime"] -= dt

                    # Remove if out of bounds or lifetime expired
                    if (bullet["lifetime"] <= 0 or
                        bullet["x"] < 0 or bullet["x"] > 800 or
                            bullet["y"] < 0 or bullet["y"] > 600):
                        bullets_to_remove.append(i)
                        continue

                    # Check collision with players
                    for pid, player in self.players.items():
                        if pid == bullet["owner_id"] or not player.get("alive", True):
                            continue

                        # Simple circle collision
                        dx = bullet["x"] - player["x"]
                        dy = bullet["y"] - player["y"]
                        dist_sq = dx*dx + dy*dy
                        if dist_sq < (15 + 5) ** 2:  # player radius + bullet radius
                            player["health"] -= 1
                            if player["health"] <= 0:
                                player["alive"] = False
                                player["health"] = 0
                            bullets_to_remove.append(i)
                            break

                # Remove bullets
                for i in sorted(bullets_to_remove, reverse=True):
                    self.game_state["bullets"].pop(i)

                # Update game state to clients
                self.game_state["players"] = dict(self.players)

            # Broadcast game state
            self.broadcast(MessageType.GAME_STATE, self.game_state)

    def start_game(self):
        """Start the game."""
        with self.lock:
            if not (len(self.ready_players) == len(self.players) and len(self.players) > 0):
                return False
            self.game_started = True
        # Lock released before broadcast to avoid deadlock (broadcast also acquires the lock)
        print("Game starting!")
        self.broadcast(MessageType.GAME_START, {})
        return True

    def stop(self):
        """Stop the server."""
        self.running = False
        with self.lock:
            for client_socket in self.clients.values():
                try:
                    client_socket.close()
                except:
                    pass
        if self.server_socket:
            self.server_socket.close()


def main():
    """Run the server."""
    import sys
    port = 5001
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    server = GameServer(port)
    server.start()

    print("\nServer commands:")
    print("  start - Start the game when all players are ready")
    print("  quit - Stop the server")

    try:
        while server.running:
            cmd = input("> ").strip().lower()
            if cmd == "quit":
                break
            elif cmd == "start":
                if server.start_game():
                    print("Game started!")
                else:
                    print(
                        "Cannot start: Not all players are ready or no players connected")
            elif cmd == "status":
                print(
                    f"Players: {len(server.players)}, Ready: {len(server.ready_players)}, Game started: {server.game_started}")
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
