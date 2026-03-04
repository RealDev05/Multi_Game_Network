"""
Game client with pygame UI, networking, and game rendering.
"""

import pygame
import socket
import threading
import sys
import math
import select
from protocol import MessageType, encode_message, decode_message
from entities import Player, Bullet, draw_text


class GameState:
    """Enum for different game states."""
    MENU = "menu"
    LOBBY = "lobby"
    GAME = "game"
    GAME_OVER = "game_over"


class GameClient:
    """Main game client handling UI and networking."""

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((800, 600))
        pygame.display.set_caption("Tank Battle")
        self.clock = pygame.time.Clock()

        # Fonts
        self.font_large = pygame.font.Font(None, 48)
        self.font_medium = pygame.font.Font(None, 36)
        self.font_small = pygame.font.Font(None, 24)

        # Game state
        self.state = GameState.MENU
        self.running = True

        # Network
        self.socket = None
        self.connected = False
        self.client_id = None
        self.my_color = None

        # Input buffers for menu
        self.input_buffer = {"host": "5001",
                             "join_ip": "localhost", "join_port": "5001"}
        self.active_input = None

        # Players
        self.players = {}  # player_id -> Player object
        self.ready_players = set()
        self.is_ready = False

        # Bullets
        self.bullets = []

        # Input state
        self.keys_down = {
            "w": False, "a": False, "s": False, "d": False,
            "left": False, "right": False, "shoot": False
        }
        self.last_input_sent = None

        # Network buffer
        self.receive_buffer = b""

        # Game over
        self.winner_id = None
        self.winner_color = None

        # Host mode
        self.is_host = False
        self.server_process = None

    def connect_to_server(self, host, port):
        """Connect to game server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((host, port))
            self.connected = True

            # Start receiving thread
            recv_thread = threading.Thread(target=self.receive_loop)
            recv_thread.daemon = True
            recv_thread.start()

            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def receive_loop(self):
        """Receive messages from server."""
        while self.connected:
            try:
                ready, _, _ = select.select([self.socket], [], [], 0)
                if not ready:
                    continue

                data = self.socket.recv(4096)
                if not data:
                    break

                self.receive_buffer += data

                # Process all complete messages
                while b'\n' in self.receive_buffer:
                    line, self.receive_buffer = self.receive_buffer.split(
                        b'\n', 1)
                    try:
                        msg_type, msg_data = decode_message(line)
                        self.process_message(msg_type, msg_data)
                    except Exception as e:
                        print(f"Error processing message: {e}")
            except Exception as e:
                print(f"Receive error: {e}")
                break

        print("Disconnected from server.")
        self.connected = False

    def process_message(self, msg_type: MessageType, data: dict):
        """Process a message from the server."""
        if msg_type == MessageType.CONNECTION_ACCEPTED:
            self.client_id = data["client_id"]
            self.my_color = tuple(data["color"])
            self.state = GameState.LOBBY
            print(
                f"Connected as player {self.client_id} with color {self.my_color}")

        elif msg_type == MessageType.PLAYER_JOINED:
            player_data = data["player"]
            player_id = player_data["id"]
            if player_id not in self.players:
                is_local = (player_id == self.client_id)
                player = Player(
                    player_id,
                    player_data["x"],
                    player_data["y"],
                    tuple(player_data["color"]),
                    is_local
                )
                player.angle = player_data.get("angle", 0)
                player.health = player_data.get("health", 3)
                player.alive = player_data.get("alive", True)
                self.players[player_id] = player

        elif msg_type == MessageType.PLAYER_LEFT:
            client_id = data["client_id"]
            if client_id in self.players:
                del self.players[client_id]
            if client_id in self.ready_players:
                self.ready_players.remove(client_id)

        elif msg_type == MessageType.PLAYER_READY:
            client_id = data["client_id"]
            self.ready_players.add(client_id)

        elif msg_type == MessageType.GAME_START:
            self.state = GameState.GAME

        elif msg_type == MessageType.GAME_OVER:
            self.winner_id = data.get("winner_id")
            self.winner_color = tuple(
                data.get("winner_color", (255, 255, 255)))
            self.state = GameState.GAME_OVER

        elif msg_type == MessageType.GAME_STATE:
            # Update all players
            players_data = data.get("players", {})
            for pid_str, pdata in players_data.items():
                pid = int(pid_str)
                if pid in self.players:
                    self.players[pid].update_from_server(pdata)

            # Update bullets
            self.bullets = []
            for bullet_data in data.get("bullets", []):
                bullet = Bullet(
                    bullet_data["x"],
                    bullet_data["y"],
                    bullet_data["vx"],
                    bullet_data["vy"],
                    bullet_data["owner_id"]
                )
                self.bullets.append(bullet)

    def send_message(self, msg_type: MessageType, data: dict = None):
        """Send a message to the server."""
        if self.connected and self.socket:
            try:
                msg = encode_message(msg_type, data)
                self.socket.send(msg)
            except Exception as e:
                print(f"Send error: {e}")
                self.connected = False

    def send_ready(self):
        """Send ready status to server."""
        self.is_ready = True
        self.send_message(MessageType.READY)

    def send_input(self):
        """Send input state to server."""
        input_data = {
            "w": self.keys_down["w"],
            "a": self.keys_down["a"],
            "s": self.keys_down["s"],
            "d": self.keys_down["d"],
            "left": self.keys_down["left"],
            "right": self.keys_down["right"],
            "shoot": self.keys_down["shoot"]
        }

        # Only send if changed
        if input_data != self.last_input_sent:
            self.send_message(MessageType.PLAYER_INPUT, input_data)
            self.last_input_sent = input_data.copy()

    def draw_menu(self):
        """Draw the main menu."""
        self.screen.fill((20, 20, 40))

        draw_text(self.screen, "TANK BATTLE", (400, 100),
                  self.font_large, center=True)

        # Host button
        host_rect = pygame.Rect(250, 250, 300, 50)
        pygame.draw.rect(self.screen, (0, 100, 200), host_rect)
        draw_text(self.screen, "HOST GAME", (400, 275),
                  self.font_medium, center=True)

        # Port input for host
        port_rect = pygame.Rect(250, 320, 300, 40)
        pygame.draw.rect(self.screen, (50, 50, 70), port_rect, 2)
        port_text = f"Port: {self.input_buffer['host']}"
        if self.active_input == "host":
            port_text += "_"
        draw_text(self.screen, port_text, (260, 330), self.font_small)

        # Join button
        join_rect = pygame.Rect(250, 400, 300, 50)
        pygame.draw.rect(self.screen, (0, 150, 0), join_rect)
        draw_text(self.screen, "JOIN GAME", (400, 425),
                  self.font_medium, center=True)

        # IP input
        ip_rect = pygame.Rect(250, 470, 200, 40)
        pygame.draw.rect(self.screen, (50, 50, 70), ip_rect, 2)
        ip_text = f"{self.input_buffer['join_ip']}"
        if self.active_input == "join_ip":
            ip_text += "_"
        draw_text(self.screen, ip_text, (260, 480), self.font_small)

        # Port input for join
        port_rect2 = pygame.Rect(460, 470, 90, 40)
        pygame.draw.rect(self.screen, (50, 50, 70), port_rect2, 2)
        port_text2 = f"{self.input_buffer['join_port']}"
        if self.active_input == "join_port":
            port_text2 += "_"
        draw_text(self.screen, port_text2, (470, 480), self.font_small)

        return host_rect, join_rect, port_rect, ip_rect, port_rect2

    def draw_lobby(self):
        """Draw the lobby screen."""
        self.screen.fill((20, 20, 40))

        draw_text(self.screen, "LOBBY", (400, 50),
                  self.font_large, center=True)

        # Connection info
        if self.is_host:
            import socket as sock
            hostname = sock.gethostname()
            local_ip = sock.gethostbyname(hostname)
            info_text = f"Server IP: {local_ip}  Port: {self.input_buffer['host']}"
            draw_text(self.screen, info_text, (400, 100),
                      self.font_small, (150, 255, 150), center=True)

        # Players list
        y_offset = 150
        for player in self.players.values():
            ready_text = " [READY]" if player.id in self.ready_players else ""
            you_text = " (YOU)" if player.id == self.client_id else ""
            player_text = f"Player {player.id}{you_text}{ready_text}"

            # Draw color indicator
            pygame.draw.circle(self.screen, player.color,
                               (260, y_offset + 10), 10)
            draw_text(self.screen, player_text,
                      (280, y_offset), self.font_medium)
            y_offset += 40

        # Ready button
        if not self.is_ready:
            ready_rect = pygame.Rect(250, 500, 300, 50)
            pygame.draw.rect(self.screen, (0, 200, 0), ready_rect)
            draw_text(self.screen, "READY", (400, 520),
                      self.font_medium, center=True)
        else:
            ready_rect = pygame.Rect(250, 500, 300, 50)
            pygame.draw.rect(self.screen, (100, 100, 100), ready_rect)
            draw_text(self.screen, "WAITING...", (400, 520),
                      self.font_medium, center=True)

        # Host start button
        start_rect = None
        if self.is_host and len(self.ready_players) == len(self.players) and len(self.players) > 1:
            start_rect = pygame.Rect(250, 420, 300, 50)
            pygame.draw.rect(self.screen, (200, 100, 0), start_rect)
            draw_text(self.screen, "START GAME", (400, 440),
                      self.font_medium, center=True)

        return ready_rect, start_rect

    def draw_game_over(self):
        """Draw the game over screen."""
        self.screen.fill((10, 10, 20))

        is_winner = (self.client_id == self.winner_id)

        if is_winner:
            draw_text(self.screen, "YOU WIN!", (400, 200),
                      self.font_large, (255, 215, 0), center=True)
        else:
            draw_text(self.screen, "YOU LOST", (400, 200),
                      self.font_large, (200, 50, 50), center=True)

        # Show winner info
        if self.winner_id is not None:
            winner_label = "Winner:"
            draw_text(self.screen, winner_label, (400, 300),
                      self.font_medium, (200, 200, 200), center=True)
            if self.winner_color:
                pygame.draw.circle(
                    self.screen, self.winner_color, (355, 350), 14)
            draw_text(self.screen, f"Player {self.winner_id}", (380, 338), self.font_medium,
                      self.winner_color if self.winner_color else (255, 255, 255))

        draw_text(self.screen, "Close the window to exit.", (400, 430),
                  self.font_small, (150, 150, 150), center=True)

    def draw_game(self):
        """Draw the game screen."""
        self.screen.fill((40, 40, 60))

        # Draw all players
        for player in self.players.values():
            player.draw(self.screen)

        # Draw all bullets
        for bullet in self.bullets:
            bullet.draw(self.screen)

        # Draw status
        if self.client_id in self.players:
            my_player = self.players[self.client_id]
            status_text = "ALIVE" if my_player.alive else "SPECTATOR"
            color = (0, 255, 0) if my_player.alive else (150, 150, 150)
            draw_text(
                self.screen, f"Status: {status_text}", (10, 10), self.font_small, color)

        # Count alive players
        alive_count = sum(1 for p in self.players.values() if p.alive)
        draw_text(self.screen, f"Players alive: {alive_count}/{len(self.players)}",
                  (10, 40), self.font_small, (255, 255, 255))

    def handle_menu_click(self, pos, host_rect, join_rect, port_rect, ip_rect, port_rect2):
        """Handle mouse clicks in menu."""
        if host_rect.collidepoint(pos):
            # Host game
            port = int(self.input_buffer["host"])
            self.is_host = True
            # Start server in separate thread
            import subprocess
            self.server_process = subprocess.Popen(
                [sys.executable, "server.py", str(port)],
                cwd=r"c:\Projects\Multi_Game_Network"
            )
            # Give server time to start
            import time
            time.sleep(0.5)
            # Connect to own server
            if self.connect_to_server("localhost", port):
                self.state = GameState.LOBBY

        elif join_rect.collidepoint(pos):
            # Join game
            ip = self.input_buffer["join_ip"]
            port = int(self.input_buffer["join_port"])
            if self.connect_to_server(ip, port):
                self.state = GameState.LOBBY

        elif port_rect.collidepoint(pos):
            self.active_input = "host"
        elif ip_rect.collidepoint(pos):
            self.active_input = "join_ip"
        elif port_rect2.collidepoint(pos):
            self.active_input = "join_port"

    def handle_lobby_click(self, pos, ready_rect, start_rect):
        """Handle mouse clicks in lobby."""
        if ready_rect and ready_rect.collidepoint(pos) and not self.is_ready:
            self.send_ready()

        if start_rect and start_rect.collidepoint(pos) and self.is_host:
            self.send_message(MessageType.REQUEST_START)

    def handle_text_input(self, text):
        """Handle text input for menu fields."""
        if self.active_input and self.state == GameState.MENU:
            if text == '\b':  # Backspace
                if len(self.input_buffer[self.active_input]) > 0:
                    self.input_buffer[self.active_input] = self.input_buffer[self.active_input][:-1]
            elif text == '\r':  # Enter
                self.active_input = None
            else:
                # Filter valid characters based on input type
                if self.active_input == "join_ip":
                    if text in "0123456789.abcdefghijklmnopqrstuvwxyz":
                        self.input_buffer[self.active_input] += text
                else:  # port fields
                    if text in "0123456789":
                        self.input_buffer[self.active_input] += text

    def run(self):
        """Main game loop."""
        menu_rects = None
        lobby_rects = None

        while self.running:
            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self.state == GameState.MENU and menu_rects:
                        self.handle_menu_click(event.pos, *menu_rects)
                    elif self.state == GameState.LOBBY and lobby_rects:
                        self.handle_lobby_click(event.pos, *lobby_rects)

                elif event.type == pygame.KEYDOWN:
                    if self.state == GameState.MENU:
                        if event.key == pygame.K_BACKSPACE:
                            self.handle_text_input('\b')
                        elif event.key == pygame.K_RETURN:
                            self.handle_text_input('\r')
                        elif event.unicode:
                            self.handle_text_input(event.unicode)

                    elif self.state == GameState.GAME:
                        # Game controls
                        if event.key == pygame.K_w:
                            self.keys_down["w"] = True
                        elif event.key == pygame.K_a:
                            self.keys_down["a"] = True
                        elif event.key == pygame.K_s:
                            self.keys_down["s"] = True
                        elif event.key == pygame.K_d:
                            self.keys_down["d"] = True
                        elif event.key == pygame.K_LEFT:
                            self.keys_down["left"] = True
                        elif event.key == pygame.K_RIGHT:
                            self.keys_down["right"] = True
                        elif event.key == pygame.K_SPACE:
                            self.keys_down["shoot"] = True

                elif event.type == pygame.KEYUP:
                    if self.state == GameState.GAME:
                        if event.key == pygame.K_w:
                            self.keys_down["w"] = False
                        elif event.key == pygame.K_a:
                            self.keys_down["a"] = False
                        elif event.key == pygame.K_s:
                            self.keys_down["s"] = False
                        elif event.key == pygame.K_d:
                            self.keys_down["d"] = False
                        elif event.key == pygame.K_LEFT:
                            self.keys_down["left"] = False
                        elif event.key == pygame.K_RIGHT:
                            self.keys_down["right"] = False
                        elif event.key == pygame.K_SPACE:
                            self.keys_down["shoot"] = False

            # Render based on state
            if self.state == GameState.MENU:
                menu_rects = self.draw_menu()
            elif self.state == GameState.LOBBY:
                lobby_rects = self.draw_lobby()
            elif self.state == GameState.GAME:
                self.draw_game()
                # Send input to server
                if self.connected:
                    self.send_input()
            elif self.state == GameState.GAME_OVER:
                self.draw_game_over()

            pygame.display.flip()
            self.clock.tick(60)

        # Cleanup
        if self.connected and self.socket:
            self.socket.close()
        if self.is_host and self.server_process:
            self.server_process.terminate()
        pygame.quit()


def main():
    """Entry point for the client."""
    client = GameClient()
    client.run()


if __name__ == "__main__":
    main()
