"""
Game entities: Player and Bullet classes with rendering and collision logic.
"""

import pygame
import math


class Player:
    """Represents a player tank."""

    def __init__(self, player_id, x, y, color, is_local=False):
        self.id = player_id
        self.x = x
        self.y = y
        self.angle = 0  # degrees
        self.color = color
        self.health = 3
        self.alive = True
        self.is_local = is_local
        self.radius = 15

    def update_from_server(self, data):
        """Update player state from server data."""
        self.x = data.get("x", self.x)
        self.y = data.get("y", self.y)
        self.angle = data.get("angle", self.angle)
        self.health = data.get("health", self.health)
        self.alive = data.get("alive", self.alive)

    def draw(self, surface):
        """Draw the player tank."""
        # Draw as a circle with a barrel line
        pos = (int(self.x), int(self.y))

        # Make ghost-like if dead
        alpha = 100 if not self.alive else 255

        # Create a surface for transparency
        if not self.alive:
            temp_surface = pygame.Surface(
                (self.radius * 2 + 10, self.radius * 2 + 10), pygame.SRCALPHA)
            pygame.draw.circle(temp_surface, (*self.color, alpha),
                               (self.radius + 5, self.radius + 5), self.radius)
            surface.blit(temp_surface, (int(self.x) -
                         self.radius - 5, int(self.y) - self.radius - 5))
        else:
            pygame.draw.circle(surface, self.color, pos, self.radius)

        # Draw barrel
        angle_rad = math.radians(self.angle)
        barrel_length = 25
        barrel_end = (
            int(self.x + math.cos(angle_rad) * barrel_length),
            int(self.y + math.sin(angle_rad) * barrel_length)
        )
        barrel_color = self.color if self.alive else (*self.color, alpha)
        pygame.draw.line(surface, barrel_color, pos, barrel_end, 3)

        # Draw health bar if alive
        if self.alive:
            health_bar_width = 30
            health_bar_height = 5
            health_x = self.x - health_bar_width / 2
            health_y = self.y - self.radius - 10

            # Background
            pygame.draw.rect(surface, (100, 100, 100),
                             (health_x, health_y, health_bar_width, health_bar_height))
            # Health
            health_width = (self.health / 3) * health_bar_width
            pygame.draw.rect(surface, (0, 255, 0),
                             (health_x, health_y, health_width, health_bar_height))


class Bullet:
    """Represents a bullet."""

    def __init__(self, x, y, vx, vy, owner_id):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.owner_id = owner_id
        self.radius = 5

    def draw(self, surface):
        """Draw the bullet."""
        pygame.draw.circle(surface, (255, 255, 0),
                           (int(self.x), int(self.y)), self.radius)


def draw_text(surface, text, pos, font, color=(255, 255, 255), center=False):
    """Helper function to draw text."""
    text_surface = font.render(text, True, color)
    text_rect = text_surface.get_rect()
    if center:
        text_rect.center = pos
    else:
        text_rect.topleft = pos
    surface.blit(text_surface, text_rect)
