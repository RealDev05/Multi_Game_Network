# Tank Battle - Multiplayer Game

A real-time multiplayer tank battle game built with Python, Pygame, and sockets. Features power-up crates, destructible obstacles, bouncy bullets, laser shots, and variable map sizes.

## Requirements

- Python 3.7+
- pygame

## Installation

```bash
pip install pygame
```

## How to Play

### Starting the Game

```bash
python main.py
```

---

### Hosting a Game (Local)

1. Click **HOST GAME** on the main menu
2. Set a port (default: `5001`)
3. In the lobby, select a **map size** (Small / Medium / Large)
4. Your local IP and port are displayed for other players to join
5. Click **READY** when ready
6. Once all players are ready, click **START GAME**

### Joining a Game

1. Click **JOIN GAME** on the main menu
2. Enter the host's IP address and port
   - Click a field to activate it, then type, or use **Ctrl+V** to paste, **Ctrl+A** to select all, **Ctrl+C** to copy
3. Click **READY** and wait for the host to start

---

### Running a Dedicated Server (VM / Always-On Host)

```bash
python server.py <port>
# Example:
python server.py 5003
```

Players join by entering the server's public IP and port in the JOIN GAME screen.

#### Server Console Commands

| Command            | Description                                               |
| ------------------ | --------------------------------------------------------- |
| `status`           | Show player count, ready count, map size, autostart state |
| `players`          | List all connected players with ready/alive status        |
| `map`              | Show current map size and available options               |
| `map <size>`       | Set map size: `small`, `medium`, or `large`               |
| `autostart on/off` | Auto-start the game as soon as all players are ready      |
| `start`            | Force-start the game immediately                          |
| `kick <id>`        | Disconnect a player by their ID                           |
| `help`             | Show all commands                                         |
| `quit`             | Shut down the server                                      |

---

## Game Controls

| Key               | Action                                      |
| ----------------- | ------------------------------------------- |
| **W / A / S / D** | Move up / left / down / right               |
| **← / →**         | Rotate turret counter-clockwise / clockwise |
| **Space**         | Shoot                                       |

Movement is **holonomic** — W always moves up regardless of where your turret is pointing.

---

## Gameplay

- Each player is a colored tank with a rotating turret
- **3 hits** to eliminate a player (health bar shown above each tank)
- Eliminated players become **spectators** (ghost mode)
- Last player standing wins

### Obstacles

- Random rectangular obstacles are generated each game
- BFS connectivity ensures no player can ever be enclosed
- Bullets collide with obstacles; bouncy bullets reflect off them

### Power-up Crates

Crates spawn on the map periodically (up to 5 at once, every 8 seconds, 12-second lifetime). Walk over one to collect it.

| Crate     | Label | Effect                                  | Rarity          |
| --------- | ----- | --------------------------------------- | --------------- |
| 🔴 Health | **H** | Restores to full health (3 HP)          | Rare (1-in-6)   |
| 🔵 Shield | **S** | Absorbs 1 hit (stackable up to 3)       | Common (1-in-3) |
| 🟣 Laser  | **L** | Next shot is an instant laser beam      | Rare (1-in-6)   |
| 🟢 Bouncy | **B** | Next 3 shots bounce off walls/obstacles | Common (1-in-3) |

Active power-ups are shown in the **bottom-left HUD** as colored diamond icons with a count.

#### Bullet Types

- **Normal** — standard bullet, destroyed on obstacle hit
- **Laser** — instant beam, hits **all** players in line of sight (passes through them)
- **Bouncy** — reflects off walls and obstacles (up to 3 bounces); can hurt the shooter _after_ the first bounce

---

## Map Sizes

| Size   | Dimensions |
| ------ | ---------- |
| Small  | 800 × 600  |
| Medium | 1100 × 750 |
| Large  | 1400 × 900 |

The host selects the map size in the lobby (or via the `map` command on a dedicated server). All clients resize their window automatically when the game starts.

---

## Files

| File          | Description                                                     |
| ------------- | --------------------------------------------------------------- |
| `main.py`     | Entry point — prints controls and launches the client           |
| `client.py`   | Pygame UI, input handling, rendering, network client            |
| `server.py`   | Authoritative game server — physics, collision, state sync      |
| `protocol.py` | Message type enum and JSON encode/decode helpers                |
| `entities.py` | Renderable entities: Player, Bullet, Obstacle, Crate, LaserBeam |

## Architecture

Client-server over TCP with newline-delimited JSON messages:

- The **server** is authoritative for all physics, collision detection, bullet movement, crate spawning, and win conditions
- **Clients** send keyboard input each frame and receive the full game state at 60 FPS to render
- The host client auto-starts a local `server.py` subprocess; a dedicated server can also be run independently
- Input uses `pygame.key.get_pressed()` polling with a force-resend every 15 frames to self-heal desyncs
