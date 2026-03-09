"""
Game entities: Player, Bullet, Obstacle, Crate, LaserBeam with rendering logic.
"""

import pygame
import math
import os

# ── Tank sprite constants ───────────────────────────────────────────────────────
# Width to render the sprite at (height scales proportionally).
# Keep diameter <= 2 * Player.radius (15) so the sprite fits the hitbox.
_TANK_SPRITE_SIZE = 28
# Angle offset: sprite barrel points DOWN in the image (+y). In-game angle=0
# means barrel points RIGHT (+x). pygame.transform.rotate is counter-clockwise.
# offset = 270 aligns down→right and also corrects the 180° flip.
_TANK_ANGLE_OFFSET = 270

_tank_base: pygame.Surface = None     # raw scaled surface, loaded once
_tank_tinted: dict = {}               # color tuple → tinted Surface (cached)

# ── Sandbag obstacle sprite ──────────────────────────────────────────────────
# Each sandbag tile is rendered at this size (proportional to 211×147 source).
_SB_W = 48
_SB_H = 34
# 25 % overlap → step between tile origins
_SB_STEP_X = int(_SB_W * 0.75)   # 36 px
_SB_STEP_Y = int(_SB_H * 0.75)   # 25 px
# Odd rows are shifted right by half a step (brick-wall pattern)
_SB_OFFSET_X = _SB_STEP_X // 2   # 18 px

_sandbag_raw: pygame.Surface = None   # original RGBA surface loaded once
_sandbag_tile: pygame.Surface = None  # single tile scaled to (_SB_W, _SB_H)
_sandbag_cache: dict = {}             # (w, h) → composed Surface


def _get_sandbag_tile() -> pygame.Surface:
    global _sandbag_raw, _sandbag_tile
    if _sandbag_tile is None:
        if _sandbag_raw is None:
            path = os.path.join(os.path.dirname(__file__),
                                "assets", "sandbag.png")
            _sandbag_raw = pygame.image.load(path).convert_alpha()
        _sandbag_tile = pygame.transform.smoothscale(
            _sandbag_raw, (_SB_W, _SB_H))
    return _sandbag_tile


def _build_sandbag_surface(w: int, h: int) -> pygame.Surface:
    """Compose a (w, h) surface filled with 25%-overlapping sandbag tiles.
    Odd rows are shifted right by half a step (brick-wall pattern).
    Only fully-visible tiles are drawn — edges may have empty space, no cut-off.
    """
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    tile = _get_sandbag_tile()
    row = 0
    y = 0
    while y + _SB_H <= h:
        x_start = _SB_OFFSET_X if row % 2 else 0   # shift odd rows right
        x = x_start
        while x + _SB_W <= w:
            surf.blit(tile, (x, y))
            x += _SB_STEP_X
        y += _SB_STEP_Y
        row += 1
    return surf


def _get_sandbag(w: int, h: int) -> pygame.Surface:
    """Return a tiled sandbag surface for obstacle size (w, h), cached."""
    key = (w, h)
    if key not in _sandbag_cache:
        _sandbag_cache[key] = _build_sandbag_surface(w, h)
    return _sandbag_cache[key]


def _load_tank_base() -> pygame.Surface:
    """Load and scale the grayscale tank sprite (once)."""
    global _tank_base
    if _tank_base is None:
        path = os.path.join(os.path.dirname(__file__),
                            "assets", "tanks_grayscale.png")
        raw = pygame.image.load(path).convert_alpha()
        h = int(_TANK_SPRITE_SIZE * raw.get_height() / raw.get_width())
        _tank_base = pygame.transform.smoothscale(raw, (_TANK_SPRITE_SIZE, h))
    return _tank_base


def _get_tinted_tank(color: tuple) -> pygame.Surface:
    """Return a cached softly color-tinted copy of the tank sprite."""
    key = color[:3]
    if key not in _tank_tinted:
        base = _load_tank_base()
        # Blend: 60% tint color + 40% original grayscale so detail stays visible.
        tinted = base.copy()
        tint_layer = base.copy()
        tint_layer.fill((*key, 255), special_flags=pygame.BLEND_RGBA_MULT)
        tinted.blit(tint_layer, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        # Restore 40% of the original brightness on top
        restore = base.copy()
        restore.fill((255, 255, 255, 100),
                     special_flags=pygame.BLEND_RGBA_MULT)
        tinted.blit(restore, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        _tank_tinted[key] = tinted
    return _tank_tinted[key]


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
        # matches server PLAYER_RADIUS (circumscribed sprite circle)
        self.radius = 20
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
        """Draw the player tank using the sprite."""
        pos = (int(self.x), int(self.y))

        # Shield ring (behind everything)
        if self.shield > 0 and self.alive:
            pygame.draw.circle(surface, (80, 160, 255),
                               pos, self.radius + 6, 2)

        # Tinted + rotated sprite
        tinted = _get_tinted_tank(self.color)
        rotated = pygame.transform.rotate(
            tinted, -(self.angle + _TANK_ANGLE_OFFSET))
        if not self.alive:
            # Fade-out for dead/spectator players (per-pixel alpha surface)
            rotated = rotated.copy()
            rotated.fill((255, 255, 255, 100),
                         special_flags=pygame.BLEND_RGBA_MULT)
        rr = rotated.get_rect(center=pos)
        surface.blit(rotated, rr)

        # Health bar
        if self.alive:
            bw, bh = 30, 5
            bx = self.x - bw / 2
            by = self.y - self.radius - 12
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
        """Draw the obstacle as a brick-laid pile of sandbag sprites."""
        surface.blit(_get_sandbag(self.w, self.h), (self.x, self.y))


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
