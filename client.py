"""
Game client with pygame UI, networking, and game rendering.
"""

import pygame
import socket
import threading
import sys
import math
import select
from protocol import MessageType, encode_message, decode_message, encode_udp, decode_udp
from entities import Player, Bullet, Obstacle, Crate, LaserBeam, draw_text


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
        pygame.scrap.init()   # clipboard support (must be after display init)
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

        # UDP fast path (opened after CONNECTION_ACCEPTED)
        self.udp_socket = None
        self.udp_server_addr = None   # (host, udp_port) — UDP send destination
        self._udp_host = None          # host string saved from connect_to_server()
        self._udp_seq = -1             # last received UDP sequence number

        # Input buffers for menu
        self.input_buffer = {"host": "5001",
                             "join_ip": "localhost", "join_port": "5001"}
        self.active_input = None
        self.input_select_all = False

        # Players
        self.players = {}  # player_id -> Player object
        self.ready_players = set()
        self.is_ready = False

        # Bullets
        self.bullets = []
        self.obstacles = []
        self.crates = {}    # crate_id -> Crate
        self.lasers = []    # list of LaserBeam

        # Input state — polled each frame via pygame.key.get_pressed()
        self.keys_down = {
            "w": False, "a": False, "s": False, "d": False,
            "left": False, "right": False, "shoot": False
        }
        self.last_input_sent = None
        # counts down; when 0, force a resend regardless of change
        self.input_force_resend = 0

        # Network buffer
        self.receive_buffer = b""

        # Game over
        self.winner_id = None
        self.winner_color = None

        # Host mode
        self.is_host = False
        self.server_process = None
        self.map_size_choice = "small"  # host's chosen map size (set in lobby)

        # Map dimensions — updated when GAME_START is received
        self.map_w = 800
        self.map_h = 600
        # (w, h) set by receive thread, applied on main thread
        self.pending_resize = None

    def connect_to_server(self, host, port):
        """Connect to game server."""
        try:
            self._udp_host = host
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

    def udp_receive_loop(self):
        """Receive UDP game-state datagrams from the server (fast path)."""
        self.udp_socket.settimeout(1.0)
        while self.connected:
            try:
                raw, _ = self.udp_socket.recvfrom(65535)
                msg_type, msg_data = decode_udp(raw)
                if msg_type == MessageType.GAME_STATE:
                    seq = msg_data.pop("seq", 0)
                    if seq > self._udp_seq:
                        self._udp_seq = seq
                        self.process_message(msg_type, msg_data)
                    # else: stale / out-of-order datagram — discard
            except socket.timeout:
                continue
            except Exception as e:
                if self.connected:
                    print(f"UDP receive error: {e}")
                break

    def process_message(self, msg_type: MessageType, data: dict):
        """Process a message from the server."""
        if msg_type == MessageType.CONNECTION_ACCEPTED:
            self.client_id = data["client_id"]
            self.my_color = tuple(data["color"])
            self.state = GameState.LOBBY
            print(
                f"Connected as player {self.client_id} with color {self.my_color}")

            # Open UDP channel on the same port the server listed
            udp_port = data.get("udp_port", 5001)
            self.udp_server_addr = (self._udp_host, udp_port)
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            reg_pkt = encode_udp(MessageType.UDP_REGISTER, {
                                 "client_id": self.client_id})
            self.udp_socket.sendto(reg_pkt, self.udp_server_addr)
            udp_thread = threading.Thread(target=self.udp_receive_loop)
            udp_thread.daemon = True
            udp_thread.start()
            print(f"UDP channel opened → {self.udp_server_addr}")

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
            map_w = data.get("map_w", 800)
            map_h = data.get("map_h", 600)
            self.map_w = map_w
            self.map_h = map_h
            self.pending_resize = (map_w, map_h)   # applied on main thread
            obstacles_data = data.get("obstacles", [])
            self.obstacles = [
                Obstacle(o["x"], o["y"], o["w"], o["h"]) for o in obstacles_data
            ]
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
                    bullet_data["owner_id"],
                    bullet_data.get("btype", "normal"),
                )
                self.bullets.append(bullet)

            # Update crates
            new_crate_ids = set()
            for cd in data.get("crates", []):
                cid = cd["id"]
                new_crate_ids.add(cid)
                if cid in self.crates:
                    self.crates[cid].update_from_server(cd)
                else:
                    self.crates[cid] = Crate(
                        cid, cd["x"], cd["y"], cd["ctype"])
            # Remove crates that disappeared
            for gone in list(self.crates.keys()):
                if gone not in new_crate_ids:
                    del self.crates[gone]

            # Update laser beams
            self.lasers = []
            for ld in data.get("lasers", []):
                beam = LaserBeam(
                    ld["x1"], ld["y1"], ld["x2"], ld["y2"],
                    tuple(ld["owner_color"]),
                )
                self.lasers.append(beam)

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
        """Poll keyboard state and send to server; force-resend every 15 frames to self-heal desyncs."""
        keys = pygame.key.get_pressed()
        input_data = {
            "w":     bool(keys[pygame.K_w]),
            "a":     bool(keys[pygame.K_a]),
            "s":     bool(keys[pygame.K_s]),
            "d":     bool(keys[pygame.K_d]),
            "left":  bool(keys[pygame.K_LEFT]),
            "right": bool(keys[pygame.K_RIGHT]),
            "shoot": bool(keys[pygame.K_SPACE]),
        }

        self.input_force_resend -= 1
        if input_data != self.last_input_sent or self.input_force_resend <= 0:
            if self.udp_socket and self.udp_server_addr:
                # Fast path: fire-and-forget over UDP
                try:
                    raw = encode_udp(MessageType.PLAYER_INPUT, input_data)
                    self.udp_socket.sendto(raw, self.udp_server_addr)
                except Exception as e:
                    print(f"UDP input send error: {e}")
                    self.send_message(MessageType.PLAYER_INPUT, input_data)
            else:
                # Fallback: TCP (e.g. UDP not yet ready)
                self.send_message(MessageType.PLAYER_INPUT, input_data)
            self.last_input_sent = input_data.copy()
            # re-sync server every 15 frames (~4x/sec at 60fps)
            self.input_force_resend = 15

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
        border_col = (255, 200, 0) if (self.active_input ==
                                       "host" and self.input_select_all) else (50, 50, 70)
        pygame.draw.rect(self.screen, border_col, port_rect, 2)
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
        border_col = (255, 200, 0) if (self.active_input ==
                                       "join_ip" and self.input_select_all) else (50, 50, 70)
        pygame.draw.rect(self.screen, border_col, ip_rect, 2)
        ip_text = f"{self.input_buffer['join_ip']}"
        if self.active_input == "join_ip":
            ip_text += "_"
        draw_text(self.screen, ip_text, (260, 480), self.font_small)

        # Port input for join
        port_rect2 = pygame.Rect(460, 470, 90, 40)
        border_col = (255, 200, 0) if (self.active_input ==
                                       "join_port" and self.input_select_all) else (50, 50, 70)
        pygame.draw.rect(self.screen, border_col, port_rect2, 2)
        port_text2 = f"{self.input_buffer['join_port']}"
        if self.active_input == "join_port":
            port_text2 += "_"
        draw_text(self.screen, port_text2, (470, 480), self.font_small)

        draw_text(self.screen, "Click a field then Ctrl+V to paste",
                  (400, 548), self.font_small, (100, 100, 140), center=True)

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

        # Map size selector (host only)
        small_rect = medium_rect = large_rect = None
        if self.is_host:
            draw_text(self.screen, "Map Size:", (400, 355),
                      self.font_small, (180, 180, 255), center=True)
            for i, (label, key) in enumerate([("Small", "small"), ("Medium", "medium"), ("Large", "large")]):
                r = pygame.Rect(155 + i * 165, 375, 150, 35)
                col = (0, 160, 70) if self.map_size_choice == key else (
                    55, 55, 85)
                pygame.draw.rect(self.screen, col, r, border_radius=5)
                pygame.draw.rect(self.screen, (180, 180, 255),
                                 r, 2, border_radius=5)
                draw_text(self.screen, label, r.center,
                          self.font_small, center=True)
                if key == "small":
                    small_rect = r
                elif key == "medium":
                    medium_rect = r
                else:
                    large_rect = r

        # Host start button
        start_rect = None
        if self.is_host and len(self.ready_players) == len(self.players) and len(self.players) > 1:
            start_rect = pygame.Rect(250, 420, 300, 50)
            pygame.draw.rect(self.screen, (200, 100, 0), start_rect)
            draw_text(self.screen, "START GAME", (400, 440),
                      self.font_medium, center=True)

        return ready_rect, start_rect, small_rect, medium_rect, large_rect

    def draw_game_over(self):
        """Draw the game over screen."""
        self.screen.fill((10, 10, 20))

        is_winner = (self.client_id == self.winner_id)
        cx = self.map_w // 2
        cy = self.map_h // 2

        if is_winner:
            draw_text(self.screen, "YOU WIN!", (cx, cy - 100),
                      self.font_large, (255, 215, 0), center=True)
        else:
            draw_text(self.screen, "YOU LOST", (cx, cy - 100),
                      self.font_large, (200, 50, 50), center=True)

        # Show winner info
        if self.winner_id is not None:
            draw_text(self.screen, "Winner:", (cx, cy),
                      self.font_medium, (200, 200, 200), center=True)
            if self.winner_color:
                pygame.draw.circle(
                    self.screen, self.winner_color, (cx - 45, cy + 50), 14)
            draw_text(self.screen, f"Player {self.winner_id}", (cx - 20, cy + 38), self.font_medium,
                      self.winner_color if self.winner_color else (255, 255, 255))

        draw_text(self.screen, "Close the window to exit.", (cx, cy + 130),
                  self.font_small, (150, 150, 150), center=True)

    def draw_game(self):
        """Draw the game screen."""
        dt = self.clock.get_time() / 1000.0
        self.screen.fill((40, 40, 60))

        # Draw obstacles first (beneath everything)
        for obstacle in self.obstacles:
            obstacle.draw(self.screen)

        # Draw crates (snapshot to avoid mutation-during-iteration)
        for crate in list(self.crates.values()):
            crate.draw(self.screen, dt)

        # Draw laser beams (above obstacles, beneath players)
        for beam in self.lasers:
            beam.draw(self.screen)

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

        # ── Power-up HUD (bottom-left) ──
        if self.client_id in self.players:
            from entities import CRATE_COLORS, _draw_diamond
            p = self.players[self.client_id]
            hud_x, hud_y = 10, self.map_h - 40
            hud_items = [
                ("shield",  CRATE_COLORS["shield"],  p.shield),
                ("laser",   CRATE_COLORS["laser"],   p.laser_shots),
                ("bouncy",  CRATE_COLORS["bouncy"],  p.bouncy_shots),
            ]
            for label, col, count in hud_items:
                if count > 0:
                    _draw_diamond(self.screen, col, hud_x + 6, hud_y, 6)
                    draw_text(self.screen, f"×{count}",
                              (hud_x + 16, hud_y - 7), self.font_small, col)
                    hud_x += 48

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
            self.input_select_all = False
        elif ip_rect.collidepoint(pos):
            self.active_input = "join_ip"
            self.input_select_all = False
        elif port_rect2.collidepoint(pos):
            self.active_input = "join_port"
            self.input_select_all = False

    def handle_lobby_click(self, pos, ready_rect, start_rect, small_rect, medium_rect, large_rect):
        """Handle mouse clicks in lobby."""
        if ready_rect and ready_rect.collidepoint(pos) and not self.is_ready:
            self.send_ready()

        if start_rect and start_rect.collidepoint(pos) and self.is_host:
            self.send_message(MessageType.REQUEST_START, {
                              "map_size": self.map_size_choice})

        if small_rect and small_rect.collidepoint(pos):
            self.map_size_choice = "small"
        elif medium_rect and medium_rect.collidepoint(pos):
            self.map_size_choice = "medium"
        elif large_rect and large_rect.collidepoint(pos):
            self.map_size_choice = "large"

    def handle_text_input(self, text):
        """Handle text input for menu fields."""
        if self.active_input and self.state == GameState.MENU:
            if text == '\b':  # Backspace
                if self.input_select_all:
                    self.input_buffer[self.active_input] = ""
                    self.input_select_all = False
                elif len(self.input_buffer[self.active_input]) > 0:
                    self.input_buffer[self.active_input] = self.input_buffer[self.active_input][:-1]
            elif text == '\r':  # Enter
                self.active_input = None
                self.input_select_all = False
            else:
                # If field is fully selected, replace it
                if self.input_select_all:
                    self.input_buffer[self.active_input] = ""
                    self.input_select_all = False
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
                        if event.key == pygame.K_a and (event.mod & pygame.KMOD_CTRL):
                            if self.active_input:
                                self.input_select_all = True
                        elif event.key == pygame.K_c and (event.mod & pygame.KMOD_CTRL):
                            if self.active_input:
                                try:
                                    text = self.input_buffer[self.active_input]
                                    pygame.scrap.put(pygame.SCRAP_TEXT,
                                                     text.encode("utf-8") + b"\x00")
                                except Exception:
                                    pass
                        elif event.key == pygame.K_v and (event.mod & pygame.KMOD_CTRL):
                            if self.active_input:
                                try:
                                    raw = pygame.scrap.get(pygame.SCRAP_TEXT)
                                    if raw:
                                        pasted = raw.decode(
                                            "utf-8", errors="ignore"
                                        ).replace("\x00", "").strip()
                                        for ch in pasted:
                                            self.handle_text_input(ch)
                                except Exception:
                                    pass
                        elif event.key == pygame.K_BACKSPACE:
                            self.handle_text_input('\b')
                        elif event.key == pygame.K_RETURN:
                            self.handle_text_input('\r')
                        elif event.unicode:
                            self.handle_text_input(event.unicode)

                elif event.type == pygame.KEYUP:
                    pass  # key state is polled via pygame.key.get_pressed() each frame

            # Render based on state
            if self.pending_resize is not None:
                self.screen = pygame.display.set_mode(self.pending_resize)
                self.pending_resize = None
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
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except Exception:
                pass
        if self.is_host and self.server_process:
            self.server_process.terminate()
        pygame.quit()


def main():
    """Entry point for the client."""
    client = GameClient()
    client.run()


if __name__ == "__main__":
    main()
