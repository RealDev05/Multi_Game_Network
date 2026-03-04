# Tank Battle - Multiplayer Game

A simple multiplayer tank battle game built with Python, Pygame, and sockets.

## Requirements

- Python 3.7+
- pygame

## Installation

```bash
pip install pygame
```

## How to Play

### Starting the Game

Run the main file:

```bash
python main.py
```

### Hosting a Game

1. Click "HOST GAME" on the main menu
2. Specify a port (default is 5001)
3. Your server IP and port will be displayed in the lobby
4. Wait for other players to join
5. Click "READY" when you're ready
6. Once all players are ready, click "START GAME" to begin

### Joining a Game

1. Click "JOIN GAME" on the main menu
2. Enter the host's IP address and port
3. Click "READY" when you're ready to play
4. Wait for the host to start the game

### Game Controls

- **W** - Move up
- **A** - Move left
- **S** - Move down
- **D** - Move right
- **Left Arrow** - Rotate turret counter-clockwise
- **Right Arrow** - Rotate turret clockwise
- **Space** - Shoot

### Gameplay

- Each player is a colored dot with a turret
- Shoot bolts at opponents to reduce their health (3 hits to eliminate)
- Eliminated players become ghosts and can spectate
- Movement is holonomic (direction doesn't affect movement - W always moves up, etc.)
- Last player standing wins!

## Files

- `main.py` - Entry point for the game
- `client.py` - Game client with pygame UI and networking
- `server.py` - Dedicated game server (auto-started when hosting)
- `protocol.py` - Network protocol definitions
- `entities.py` - Game entities (Player, Bullet classes)

## Features

- Real-time multiplayer action
- Unique color assignment for each player
- Lobby system with ready status
- Host-controlled game start
- Ghost mode for eliminated players
- Health bars
- Smooth 60 FPS gameplay

## Architecture

The game uses a client-server architecture:

- The server manages game state and synchronizes all clients
- Clients send input commands and receive game state updates
- All game logic (collision detection, bullet physics) runs on the server
- Clients render the game based on server updates

Enjoy the game!
