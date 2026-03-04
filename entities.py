"""
Game entities: Player, Bullet, Obstacle, Crate, LaserBeam with rendering logic.
"""

import pygame
import math

# Crate colours
CRATE_COLORS = {
    "health": (220, 50,  50),
    "shield": (50,  120, 255),
    "laser":  (180, 50,  255),
    "bouncy": (50,  210, 80),
}
CRATE_LABELS = {"health": "H", "shield": "S", "laser": "L", "bouncy": "B"}


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
        # power-ups (tracked client-side for rendering only)
        self.shield = 0
        self.laser_shots = 0
        self.bouncy_shots = 0

    def update_from_server(self, data):
        """Update player state from server data."""
        self.x = data.get("x", self.x)
        self.y = data.get("y", self.y)
        self.angle = data.get("angle", self.angle)
        self.health = data.get("health", self.health)
        self.alive = data.get("alive", self.alive)
        self.shield = data.get("shield", self.shield)
        self.laser_shots = data.get("laser_shots", self.laser_shots)
        self.bouncy_shots = data.get("bouncy_shots", self.bouncy_shots)

    def draw(self, surface):
        """Draw the player tank."""
        pos = (int(self.x), int(self.y))
        alpha = 100 if not self.alive else 255

        # Shield ring (drawn behind the tank body)
        if self.shield > 0 and self.alive:
            pygame.draw.circle(surface, (80, 160, 255),
                               pos, self.radius + 5, 2)

        # Tank body
        if not self.alive:
            temp = pygame.Surface(
                (self.radius * 2 + 10, self.radius * 2 + 10), pygame.SRCALPHA)
            pygame.draw.circle(temp, (*self.color, alpha),
                               (self.radius + 5, self.radius + 5), self.radius)
            surface.blit(temp, (int(self.x) - self.radius - 5,
                                int(self.y) - self.radius - 5))
        else:
            pygame.draw.circle(surface, self.color, pos, self.radius)

        # Barrel — tinted by active power-up (laser > bouncy > normal)
        angle_rad = math.radians(self.angle)
        barrel_length = 25
        barrel_end = (
            int(self.x + math.cos(angle_rad) * barrel_length),
            int(self.y + math.sin(angle_rad) * barrel_length)
        )
        if self.alive:
            if self.laser_shots > 0:
                barrel_col = CRATE_COLORS["laser"]
            elif self.bouncy_shots > 0:
                barrel_col = CRATE_COLORS["bouncy"]
            else:
                barrel_col = self.color
        else:
            barrel_col = (*self.color, alpha)
        pygame.draw.line(surface, barrel_col, pos, barrel_end, 3)

        # Health bar
        if self.alive:
            bw, bh = 30, 5
            bx = self.x - bw / 2
            by = self.y - self.radius - 10
            pygame.draw.rect(surface, (100, 100, 100), (bx, by, bw, bh))
            pygame.draw.rect(surface, (0, 255, 0),
                             (bx, by, (self.health / 3) * bw, bh))

            # Mini power-up diamonds above health bar
            icons = []
            for _ in range(self.shield):
                icons.append(CRATE_COLORS["shield"])
            for _ in range(self.laser_shots):
                icons.append(CRATE_COLORS["laser"])
            for _ in range(self.bouncy_shots):
                icons.append(CRATE_COLORS["bouncy"])
            ix = self.x - len(icons) * 7 / 2
            iy = by - 10
            for ic in icons:
                _draw_diamond(surface, ic, int(ix + 3), int(iy), 4)
                ix += 7


def _draw_diamond(surface, color, cx, cy, size):
    """Draw a small filled diamond centred at (cx, cy)."""
    pts = [(cx, cy - size), (cx + size, cy),
           (cx, cy + size), (cx - size, cy)]
    pygame.draw.polygon(surface, color, pts)


class Bullet:
    """Represents a bullet."""

    def __init__(self, x, y, vx, vy, owner_id, btype="normal"):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.owner_id = owner_id
        self.btype = btype   # "normal" | "bouncy"
        self.radius = 5

    def draw(self, surface):
        """Draw the bullet; bouncy bullets are green."""
        color = CRATE_COLORS["bouncy"] if self.btype == "bouncy" else (
            255, 220, 0)
        pygame.draw.circle(surface, color,
                           (int(self.x), int(self.y)), self.radius)


class Obstacle:
    """Represents a static rectangular obstacle."""

    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.rect = pygame.Rect(x, y, w, h)

    def draw(self, surface):
        """Draw the obstacle as a stone-like block with bevelled edges."""
        # Main fill
        pygame.draw.rect(surface, (75, 75, 85), self.rect)
        # Top-left highlight
        pygame.draw.line(surface, (115, 115, 128),
                         (self.x, self.y), (self.x + self.w - 1, self.y), 2)
        pygame.draw.line(surface, (115, 115, 128),
                         (self.x, self.y), (self.x, self.y + self.h - 1), 2)
        # Bottom-right shadow
        pygame.draw.line(surface, (38, 38, 46),
                         (self.x + self.w, self.y),
                         (self.x + self.w, self.y + self.h), 2)
        pygame.draw.line(surface, (38, 38, 46),
                         (self.x, self.y + self.h),
                         (self.x + self.w, self.y + self.h), 2)
        # Thin outer border
        pygame.draw.rect(surface, (52, 52, 62), self.rect, 1)


class Crate:
    """A collectible power-up crate rendered as a rotating diamond."""

    def __init__(self, crate_id, x, y, ctype):
        self.id = crate_id
        self.x = x
        self.y = y
        self.ctype = ctype          # "health" | "shield" | "laser" | "bouncy"
        self.size = 10
        # rotation in degrees (animated client-side)
        self.angle = 0

    def update_from_server(self, data):
        self.x = data["x"]
        self.y = data["y"]
        self.ctype = data["ctype"]

    def draw(self, surface, dt=0):
        """Draw a rotating diamond with a letter label."""
        self.angle = (self.angle + 90 * dt) % 360
        color = CRATE_COLORS[self.ctype]
        label = CRATE_LABELS[self.ctype]
        cx, cy = int(self.x), int(self.y)
        s = self.size
        ar = math.radians(self.angle)

        # Rotate the four diamond tips
        tips_local = [(0, -s), (s, 0), (0, s), (-s, 0)]
        pts = [
            (cx + tx * math.cos(ar) - ty * math.sin(ar),
             cy + tx * math.sin(ar) + ty * math.cos(ar))
            for tx, ty in tips_local
        ]
        pygame.draw.polygon(surface, color, pts)
        pygame.draw.polygon(surface, (255, 255, 255), pts, 1)

        # Label — use a tiny inline render to avoid needing a font reference
        font = pygame.font.Font(None, 18)
        txt = font.render(label, True, (255, 255, 255))
        tr = txt.get_rect(center=(cx, cy))
        surface.blit(txt, tr)


class LaserBeam:
    """A brief instant laser beam rendered as a glowing line."""

    def __init__(self, x1, y1, x2, y2, owner_color):
        self.x1, self.y1 = x1, y1
        self.x2, self.y2 = x2, y2
        self.owner_color = owner_color
        self.lifetime = 0.15   # seconds visible

    def draw(self, surface):
        """Draw a thick glow + thin inner line."""
        p1 = (int(self.x1), int(self.y1))
        p2 = (int(self.x2), int(self.y2))
        # Outer glow (semi-transparent purple-white)
        pygame.draw.line(surface, (200, 100, 255), p1, p2, 6)
        # Inner bright core
        pygame.draw.line(surface, (255, 230, 255), p1, p2, 2)


def draw_text(surface, text, pos, font, color=(255, 255, 255), center=False):
    """Helper function to draw text."""
    text_surface = font.render(text, True, color)
    text_rect = text_surface.get_rect()
    if center:
        text_rect.center = pos
    else:
        text_rect.topleft = pos
    surface.blit(text_surface, text_rect)
