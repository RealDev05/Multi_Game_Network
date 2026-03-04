"""
Main entry point for the Tank Battle game.
Run this file to start the game client.
"""

from client import main

if __name__ == "__main__":
    print("=" * 50)
    print("TANK BATTLE - Multiplayer Game")
    print("=" * 50)
    print("\nLaunching game client...")
    print("\nControls:")
    print("  WASD - Move (W=Up, S=Down, A=Left, D=Right)")
    print("  Left/Right Arrow - Rotate turret")
    print("  Space - Shoot")
    print("\nStarting...\n")

    main()
