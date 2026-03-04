"""
Game server for multiplayer tank game.
Handles client connections, lobby management, and game state synchronization.
"""

import socket
import threading
import json
import time
import math
import random
from collections import deque
from typing import Dict
from protocol import MessageType, encode_message, decode_message

# ── Obstacle generation constants ──────────────────────────────────────────────
MAP_W, MAP_H = 800, 600
PLAYER_RADIUS = 15
OBS_MIN_W, OBS_MAX_W = 40, 120
OBS_MIN_H, OBS_MAX_H = 40, 100
OBS_WALL_MARGIN = 30    # obstacles stay at least this far from every wall
OBS_GAP = 30    # minimum clear gap between any two obstacles
OBS_CLEAR_RADIUS = 70    # no obstacle within this px radius of any spawn point
MAX_OBS_COVERAGE = 0.20  # obstacles may cover at most this fraction of map area
MAX_OBSTACLES = 20
MAX_FAILURES = 300   # consecutive placement failures before giving up
BFS_CELL = 20    # grid cell size for BFS connectivity check

# Spread-out spawn positions kept clear of obstacles
SPAWN_POSITIONS = [
    (100, 100), (700, 100), (100, 500), (700, 500),
    (400,  80), (400, 520), (80, 300), (720, 300),
    (250, 200), (550, 400),
]

# ── Crate constants ───────────────────────────────────────────────────────────────
# shield and bouncy appear twice → 2× more likely than health and laser
CRATE_TYPES = ["shield", "shield", "bouncy", "bouncy", "health", "laser"]
CRATE_PICKUP_RADIUS = 20      # px — walk over to collect
CRATE_LIFETIME = 12.0    # seconds before disappearing
CRATE_SPAWN_INTERVAL = 8.0    # seconds between spawn attempts
MAX_CRATES = 5       # max crates on map at once
CRATE_WALL_MARGIN = 25      # keep away from edges
CRATE_OBS_MARGIN = 18      # keep away from obstacle edges
CRATE_SPAWN_ATTEMPTS = 100    # max random attempts per spawn
MAX_SHIELD = 3       # maximum shield stacks
BOUNCY_SHOTS_PER_CRATE = 3   # bouncy bullets granted per crate
BOUNCY_MAX_BOUNCES = 3       # how many times a bouncy bullet can reflect
LASER_BULLET_SPEED = 0       # lasers are instant — not a moving projectile


# ── Obstacle generation helpers ─────────────────────────────────────────────────

def _build_blocked_grid(obstacles):
    """Return (blocked[col][row], cols, rows).
    A cell is blocked if a player circle centred there would touch a wall or obstacle."""
    cols = MAP_W // BFS_CELL
    rows = MAP_H // BFS_CELL
    blocked = [[False] * rows for _ in range(cols)]
    for c in range(cols):
        for r in range(rows):
            cx = c * BFS_CELL + BFS_CELL // 2
            cy = r * BFS_CELL + BFS_CELL // 2
            # Wall clearance
            if cx < PLAYER_RADIUS or cx > MAP_W - PLAYER_RADIUS:
                blocked[c][r] = True
                continue
            if cy < PLAYER_RADIUS or cy > MAP_H - PLAYER_RADIUS:
                blocked[c][r] = True
                continue
            # Obstacle clearance — circle-vs-rect distance
            for ox, oy, ow, oh in obstacles:
                clx = max(ox, min(cx, ox + ow))
                cly = max(oy, min(cy, oy + oh))
                if (cx - clx) ** 2 + (cy - cly) ** 2 < PLAYER_RADIUS ** 2:
                    blocked[c][r] = True
                    break
    return blocked, cols, rows


def _is_map_connected(obstacles):
    """BFS flood fill: True only if every passable cell is reachable from every other."""
    blocked, cols, rows = _build_blocked_grid(obstacles)

    start = None
    free_count = 0
    for c in range(cols):
        for r in range(rows):
            if not blocked[c][r]:
                free_count += 1
                if start is None:
                    start = (c, r)

    if start is None:
        return False

    visited = {start}
    queue = deque([start])
    while queue:
        c, r = queue.popleft()
        for dc, dr in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nb = (c + dc, r + dr)
            if (0 <= nb[0] < cols and 0 <= nb[1] < rows
                    and nb not in visited and not blocked[nb[0]][nb[1]]):
                visited.add(nb)
                queue.append(nb)

    return len(visited) == free_count


def _obstacles_overlap(nx, ny, nw, nh, placed):
    """True if candidate rect (with OBS_GAP clearance) overlaps any placed obstacle."""
    for ox, oy, ow, oh in placed:
        if (nx < ox + ow + OBS_GAP and nx + nw + OBS_GAP > ox and
                ny < oy + oh + OBS_GAP and ny + nh + OBS_GAP > oy):
            return True
    return False


def _near_spawn(nx, ny, nw, nh):
    """True if any spawn point falls within OBS_CLEAR_RADIUS of the candidate rect."""
    for sx, sy in SPAWN_POSITIONS:
        clx = max(nx, min(sx, nx + nw))
        cly = max(ny, min(sy, ny + nh))
        if (sx - clx) ** 2 + (sy - cly) ** 2 < OBS_CLEAR_RADIUS ** 2:
            return True
    return False


def _crate_spawn_pos(obstacles):
    """Return (x, y) for a new crate not inside any obstacle, or None on failure."""
    for _ in range(CRATE_SPAWN_ATTEMPTS):
        x = random.randint(CRATE_WALL_MARGIN, MAP_W - CRATE_WALL_MARGIN)
        y = random.randint(CRATE_WALL_MARGIN, MAP_H - CRATE_WALL_MARGIN)
        blocked = False
        for obs in obstacles:
            ox, oy, ow, oh = obs["x"], obs["y"], obs["w"], obs["h"]
            if (ox - CRATE_OBS_MARGIN <= x <= ox + ow + CRATE_OBS_MARGIN and
                    oy - CRATE_OBS_MARGIN <= y <= oy + oh + CRATE_OBS_MARGIN):
                blocked = True
                break
        if not blocked:
            return x, y
    return None


def cast_laser(ox, oy, angle_deg, players, obstacles, shooter_id):
    """
    Cast an instant laser from (ox, oy) along angle_deg.
    Returns (hits, x2, y2) where hits is a list of player dicts that were struck
    and (x2, y2) is where the beam ends (first wall boundary, goes through everything).
    The laser ignores obstacles but hits all alive enemies in order along the ray.
    """
    angle_rad = math.radians(angle_deg)
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)

    # Find wall boundary t
    t_max = float('inf')
    if dx > 0:
        t_max = min(t_max, (MAP_W - ox) / dx)
    elif dx < 0:
        t_max = min(t_max, (0 - ox) / dx)
    if dy > 0:
        t_max = min(t_max, (MAP_H - oy) / dy)
    elif dy < 0:
        t_max = min(t_max, (0 - oy) / dy)
    t_max = max(t_max, 0)

    x2 = ox + dx * t_max
    y2 = oy + dy * t_max

    # Check every alive enemy for intersection with the ray
    hits = []
    for pid, player in players.items():
        if pid == shooter_id or not player.get("alive", True):
            continue
        px, py = player["x"], player["y"]
        r = PLAYER_RADIUS
        # Project player centre onto ray, clamp to [0, t_max]
        fx, fy = px - ox, py - oy
        t = fx * dx + fy * dy
        t = max(0, min(t, t_max))
        cx = ox + dx * t - px
        cy = oy + dy * t - py
        if cx * cx + cy * cy <= r * r:
            hits.append(player)
    return hits, x2, y2


def _apply_crate(ctype, player):
    """Apply a crate effect to a player dict."""
    if ctype == "health":
        player["health"] = 3
    elif ctype == "shield":
        player["shield"] = min(player.get("shield", 0) + 1, MAX_SHIELD)
    elif ctype == "laser":
        player["laser_shots"] = player.get("laser_shots", 0) + 1
    elif ctype == "bouncy":
        player["bouncy_shots"] = player.get(
            "bouncy_shots", 0) + BOUNCY_SHOTS_PER_CRATE


def generate_obstacles():
    """
    Randomly place rectangular obstacles subject to:
      - BFS connectivity  : no enclosed region (walls included).
      - Spawn clearance   : OBS_CLEAR_RADIUS around every SPAWN_POSITION.
      - Inter-obstacle gap: OBS_GAP between every pair of rects.
      - Wall margin       : OBS_WALL_MARGIN from every edge.
      - Coverage cap      : <= MAX_OBS_COVERAGE of total map area.
      - Count cap         : <= MAX_OBSTACLES rects.
    Returns list of dicts {x, y, w, h}.
    """
    placed = []       # accepted (x, y, w, h) tuples
    total_area = 0
    max_area = MAP_W * MAP_H * MAX_OBS_COVERAGE
    failures = 0

    while len(placed) < MAX_OBSTACLES and failures < MAX_FAILURES and total_area < max_area:
        w = random.randint(OBS_MIN_W, OBS_MAX_W)
        h = random.randint(OBS_MIN_H, OBS_MAX_H)
        x = random.randint(OBS_WALL_MARGIN, MAP_W - OBS_WALL_MARGIN - w)
        y = random.randint(OBS_WALL_MARGIN, MAP_H - OBS_WALL_MARGIN - h)

        if _obstacles_overlap(x, y, w, h, placed):
            failures += 1
            continue
        if _near_spawn(x, y, w, h):
            failures += 1
            continue
        if not _is_map_connected(placed + [(x, y, w, h)]):
            failures += 1
            continue

        placed.append((x, y, w, h))
        total_area += w * h
        failures = 0   # reset consecutive-failure counter on success

    print(f"Generated {len(placed)} obstacles "
          f"({total_area / (MAP_W * MAP_H) * 100:.1f}% map coverage)")
    return [{"x": x, "y": y, "w": w, "h": h} for x, y, w, h in placed]


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
            "bullets": [],
            "crates":  [],
            "lasers":  [],
        }
        self.obstacles = []  # set once at game start by generate_obstacles()
        self._crates = {}    # crate_id -> crate dict
        self._next_crate_id = 1
        self._crate_spawn_timer = CRATE_SPAWN_INTERVAL
        self._pending_game_over = None

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
            spawn_pos = SPAWN_POSITIONS[(client_id - 1) % len(SPAWN_POSITIONS)]
            self.players[client_id] = {
                "id": client_id,
                "color": color,
                "x": float(spawn_pos[0]),
                "y": float(spawn_pos[1]),
                "angle": 0,
                "health": 3,
                "alive": True,
                "shield": 0,
                "laser_shots": 0,
                "bouncy_shots": 0,
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
                        angle_rad = math.radians(player["angle"])

                        if player.get("laser_shots", 0) > 0:
                            # Instant laser
                            player["laser_shots"] -= 1
                            hits, x2, y2 = cast_laser(
                                player["x"], player["y"], player["angle"],
                                self.players, self.obstacles, client_id)
                            for hit_player in hits:
                                if hit_player.get("shield", 0) > 0:
                                    hit_player["shield"] -= 1
                                else:
                                    hit_player["health"] -= 1
                                    if hit_player["health"] <= 0:
                                        hit_player["alive"] = False
                                        hit_player["health"] = 0
                            # Store laser beam for clients (disappears after one broadcast cycle)
                            self.game_state["lasers"].append({
                                "x1": player["x"], "y1": player["y"],
                                "x2": x2, "y2": y2,
                                "owner_color": list(player["color"]),
                                "lifetime": 0.15,
                            })
                        else:
                            btype = "bouncy" if player.get(
                                "bouncy_shots", 0) > 0 else "normal"
                            if btype == "bouncy":
                                player["bouncy_shots"] -= 1
                            bullet = {
                                "owner_id": client_id,
                                "x": player["x"],
                                "y": player["y"],
                                "vx": math.cos(angle_rad) * 400,
                                "vy": math.sin(angle_rad) * 400,
                                "lifetime": 3.0,
                                "btype": btype,
                                "bounces": 0,
                            }
                            self.game_state["bullets"].append(bullet)
                        player["shot_cooldown"] = True
                        player["cooldown_timer"] = 0.4

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

                    # Resolve player-vs-obstacle collisions (circle push-out)
                    px, py = player["x"], player["y"]
                    for obs in self.obstacles:
                        ox, oy, ow, oh = obs["x"], obs["y"], obs["w"], obs["h"]
                        clx = max(ox, min(px, ox + ow))
                        cly = max(oy, min(py, oy + oh))
                        dx = px - clx
                        dy = py - cly
                        dist_sq = dx * dx + dy * dy
                        if dist_sq == 0:
                            # Centre is inside rect: push along shortest penetration axis
                            over_l = px - ox + PLAYER_RADIUS
                            over_r = ox + ow - px + PLAYER_RADIUS
                            over_t = py - oy + PLAYER_RADIUS
                            over_b = oy + oh - py + PLAYER_RADIUS
                            mn = min(over_l, over_r, over_t, over_b)
                            if mn == over_l:
                                px -= over_l
                            elif mn == over_r:
                                px += over_r
                            elif mn == over_t:
                                py -= over_t
                            else:
                                py += over_b
                        elif dist_sq < PLAYER_RADIUS * PLAYER_RADIUS:
                            dist = math.sqrt(dist_sq)
                            overlap = PLAYER_RADIUS - dist
                            px += (dx / dist) * overlap
                            py += (dy / dist) * overlap
                    player["x"] = max(20, min(780, px))
                    player["y"] = max(20, min(580, py))

                # Update bullets
                bullets_to_remove = []
                for i, bullet in enumerate(self.game_state["bullets"]):
                    bullet["x"] += bullet["vx"] * dt
                    bullet["y"] += bullet["vy"] * dt
                    bullet["lifetime"] -= dt

                    # Remove if out of bounds or lifetime expired.
                    # Bouncy bullets with remaining bounces are NOT removed here —
                    # the wall-bounce block below will reflect them first.
                    can_bounce = (bullet.get("btype") == "bouncy" and
                                  bullet.get("bounces", 0) < BOUNCY_MAX_BOUNCES)
                    if (bullet["lifetime"] <= 0 or
                        (not can_bounce and (
                            bullet["x"] < 0 or bullet["x"] > MAP_W or
                            bullet["y"] < 0 or bullet["y"] > MAP_H))):
                        bullets_to_remove.append(i)
                        continue

                    # Check collision with obstacles
                    hit_obstacle = False
                    for obs in self.obstacles:
                        ox, oy, ow, oh = obs["x"], obs["y"], obs["w"], obs["h"]
                        if (ox <= bullet["x"] <= ox + ow and
                                oy <= bullet["y"] <= oy + oh):
                            if bullet.get("btype") == "bouncy" and bullet.get("bounces", 0) < BOUNCY_MAX_BOUNCES:
                                # Determine which axis to reflect on
                                # Push bullet out and flip velocity
                                over_l = bullet["x"] - ox
                                over_r = ox + ow - bullet["x"]
                                over_t = bullet["y"] - oy
                                over_b = oy + oh - bullet["y"]
                                mn = min(over_l, over_r, over_t, over_b)
                                if mn in (over_l, over_r):
                                    bullet["vx"] *= -1
                                    bullet["x"] += -5 if mn == over_l else 5
                                else:
                                    bullet["vy"] *= -1
                                    bullet["y"] += -5 if mn == over_t else 5
                                bullet["bounces"] = bullet.get(
                                    "bounces", 0) + 1
                            else:
                                bullets_to_remove.append(i)
                                hit_obstacle = True
                            break
                    if hit_obstacle:
                        continue

                    # Check collision with players
                    for pid, player in self.players.items():
                        is_shooter = pid == bullet["owner_id"]
                        if is_shooter:
                            # Normal/laser bullets never hurt the shooter.
                            # Bouncy bullets only hurt the shooter after ≥1 bounce
                            # so the bullet can't self-hit the instant it's fired.
                            if bullet.get("btype") != "bouncy":
                                continue
                            if bullet.get("bounces", 0) < 1:
                                continue
                        if not player.get("alive", True):
                            continue

                        # Simple circle collision
                        dx = bullet["x"] - player["x"]
                        dy = bullet["y"] - player["y"]
                        dist_sq = dx*dx + dy*dy
                        if dist_sq < (15 + 5) ** 2:  # player radius + bullet radius
                            if player.get("shield", 0) > 0:
                                # shield absorbs the hit
                                player["shield"] -= 1
                            else:
                                player["health"] -= 1
                                if player["health"] <= 0:
                                    player["alive"] = False
                                    player["health"] = 0
                            bullets_to_remove.append(i)
                            break

                # Bouncy bullets bounce off walls
                for bullet in self.game_state["bullets"]:
                    if bullet.get("btype") != "bouncy":
                        continue
                    bounces = bullet.get("bounces", 0)
                    if bounces >= BOUNCY_MAX_BOUNCES:
                        continue
                    reflected = False
                    if bullet["x"] <= 0 or bullet["x"] >= MAP_W:
                        bullet["vx"] *= -1
                        bullet["x"] = max(1, min(MAP_W - 1, bullet["x"]))
                        reflected = True
                    if bullet["y"] <= 0 or bullet["y"] >= MAP_H:
                        bullet["vy"] *= -1
                        bullet["y"] = max(1, min(MAP_H - 1, bullet["y"]))
                        reflected = True
                    if reflected:
                        bullet["bounces"] = bounces + 1

                # Remove bullets
                for i in sorted(bullets_to_remove, reverse=True):
                    self.game_state["bullets"].pop(i)

                # Decay laser lifetime
                self.game_state["lasers"] = [
                    la for la in self.game_state["lasers"]
                    if la["lifetime"] - dt > 0
                ]
                for la in self.game_state["lasers"]:
                    la["lifetime"] -= dt

                # ── Crate spawning ──
                self._crate_spawn_timer -= dt
                if (self._crate_spawn_timer <= 0 and
                        len(self._crates) < MAX_CRATES and
                        self.game_started):
                    pos = _crate_spawn_pos(self.obstacles)
                    if pos:
                        cid = self._next_crate_id
                        self._next_crate_id += 1
                        ctype = random.choice(CRATE_TYPES)
                        self._crates[cid] = {
                            "id": cid, "x": float(pos[0]), "y": float(pos[1]),
                            "ctype": ctype, "lifetime": CRATE_LIFETIME,
                        }
                    self._crate_spawn_timer = CRATE_SPAWN_INTERVAL

                # ── Crate lifetime & pickup ──
                expired_crates = []
                for cid, crate in list(self._crates.items()):
                    crate["lifetime"] -= dt
                    if crate["lifetime"] <= 0:
                        expired_crates.append(cid)
                        continue
                    for player in self.players.values():
                        if not player.get("alive", True):
                            continue
                        dx = player["x"] - crate["x"]
                        dy = player["y"] - crate["y"]
                        if dx * dx + dy * dy < CRATE_PICKUP_RADIUS ** 2:
                            _apply_crate(crate["ctype"], player)
                            expired_crates.append(cid)
                            break
                for cid in expired_crates:
                    self._crates.pop(cid, None)

                # Build crate list for broadcast
                self.game_state["crates"] = list(self._crates.values())

                # Update game state to clients
                self.game_state["players"] = dict(self.players)

                # Check win condition: only 1 alive player remaining
                alive = [pid for pid, p in self.players.items()
                         if p.get("alive", True)]
                if len(alive) == 1 and self.game_started:
                    winner_id = alive[0]
                    self.game_started = False
                    winner_color = self.players[winner_id]["color"]
                    print(f"Game over! Player {winner_id} wins!")
                    self._pending_game_over = {
                        "winner_id": winner_id, "winner_color": winner_color}

            # Broadcast game state
            self.broadcast(MessageType.GAME_STATE, self.game_state)

            # Send game over outside the lock
            if hasattr(self, '_pending_game_over') and self._pending_game_over:
                self.broadcast(MessageType.GAME_OVER, self._pending_game_over)
                self._pending_game_over = None

    def start_game(self):
        """Start the game."""
        with self.lock:
            if not (len(self.ready_players) == len(self.players) and len(self.players) > 1):
                return False
            self.game_started = True
        # Generate obstacles outside the lock (CPU work + avoids broadcast deadlock)
        self.obstacles = generate_obstacles()
        print("Game starting!")
        self.broadcast(MessageType.GAME_START, {"obstacles": self.obstacles})
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
