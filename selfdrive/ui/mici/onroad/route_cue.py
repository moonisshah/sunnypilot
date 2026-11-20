"""Read-only route display. Coordinates are local metres east/north."""
import json
import math
from time import monotonic
from bisect import bisect_left
from itertools import accumulate
from pathlib import Path

ROUTE_FILE = Path('/data/media/0/santa-fe-route.json')



MAX_ROUTE_BYTES = 2_000_000
DISPLAY_ACK = Path('/dev/shm/santa-fe-route-loaded')


def route_version(path):
  stat = path.stat()
  return f'{stat.st_ino}-{stat.st_mtime_ns}-{stat.st_size}'


def finite_pair(p):
  return isinstance(p, list) and len(p) == 2 and all(type(v) in (float, int) and math.isfinite(v) and abs(v) < 12000 for v in p)


class RouteCue:
  def __init__(self, route):
    if route.get('format') != 'local-route-2':
      raise ValueError('Unsupported route format')
    self.destination = route.get('destination', 'Saved route')
    if not isinstance(self.destination, str) or not self.destination.isprintable() or len(self.destination) > 120:
      raise ValueError('Invalid destination')
    origin = route['origin']
    if not finite_pair(origin) or abs(origin[0]) > 180 or abs(origin[1]) >= 90:
      raise ValueError('Invalid route origin')
    self.lon, self.lat = origin
    self.points = route['points']
    self.turns = route['turns']
    if not 2 <= len(self.points) <= 5000 or not all(map(finite_pair, self.points)):
      raise ValueError('Invalid route geometry')
    self.lengths = list(accumulate((math.dist(a, b) for a, b in zip(self.points, self.points[1:])), initial=0.0))
    if not 1 <= len(self.turns) <= 1000:
      raise ValueError('Invalid turn count')
    previous = -1
    for distance, cue in self.turns:
      if type(distance) not in (int, float) or not previous <= distance <= self.lengths[-1] + 1 or distance < 0:
        raise ValueError('Invalid turn position')
      if not isinstance(cue, str) or not cue.isprintable() or not 1 <= len(cue) <= 120:
        raise ValueError('Invalid turn text')
      previous = distance
    self.distances = [turn[0] for turn in self.turns]

  @classmethod
  def load(cls, path=ROUTE_FILE):
    try:
      with path.open('rb') as source:
        data = source.read(MAX_ROUTE_BYTES + 1)
      if len(data) <= MAX_ROUTE_BYTES:
        return parse_route(json.loads(data))
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError, OverflowError, RecursionError):
      pass
    return None

  def cue_at(self, lat, lon):
    p = ((lon - self.lon) * 111320 * math.cos(math.radians(self.lat)), (lat - self.lat) * 111320)
    if not all(math.isfinite(v) for v in p):
      return ''
    best_offset, along = float('inf'), 0.0
    for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
      dx, dy = b[0] - a[0], b[1] - a[1]
      squared = dx * dx + dy * dy
      fraction = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / squared)) if squared else 0
      offset = math.hypot(p[0] - a[0] - fraction * dx, p[1] - a[1] - fraction * dy)
      if offset < best_offset:
        best_offset, along = offset, self.lengths[i] + fraction * math.sqrt(squared)
    if best_offset > 30:
      return 'Outside planned route'
    if self.lengths[-1] - along <= 10 and math.dist(p, self.points[-1]) <= 12:
      return 'At destination'
    index = min(bisect_left(self.distances, along), len(self.turns) - 1)
    distance, cue = self.turns[index]
    return f'{round(max(0, distance - along))} m  {cue}'


def decode_shape(shape):
  if not isinstance(shape, str) or not 4 <= len(shape) <= MAX_ROUTE_BYTES:
    raise ValueError('Invalid route shape')
  coords = [0, 0]
  values = 0
  shift = value = 0
  for char in shape:
    part = ord(char) - 63
    if not 0 <= part <= 63 or shift > 30:
      raise ValueError('Invalid polyline')
    value |= (part & 31) << shift
    if part >= 32:
      shift += 5
      continue
    coords[values % 2] += ~(value >> 1) if value & 1 else value >> 1
    values += 1
    value = shift = 0
    if values % 2 == 0:
      lat, lon = (v / 1e6 for v in coords)
      if not (-56 <= lat <= -8 and 95 <= lon <= 169) or values > 300000:
        raise ValueError('Route exceeds supported Australian coverage')
      yield lat, lon
  if shift or values % 2 or values < 4:
    raise ValueError('Incomplete route shape')


class NationalRoute:
  """Match only nearby blocks of a geographic polyline for each GPS update."""
  block_size = 64

  def __init__(self, route):
    self.destination = route['destination']
    if not isinstance(self.destination, str) or not 1 <= len(self.destination) <= 120 or not self.destination.isprintable():
      raise ValueError('Invalid destination')
    self.points = list(decode_shape(route['shape']))
    self.lengths = [0.]
    self.blocks = []
    for start in range(0, len(self.points) - 1, self.block_size):
      points = self.points[start:start + self.block_size + 1]
      lats, lons = zip(*points)
      self.blocks.append((start, min(lats), max(lats), min(lons), max(lons)))
      for (lat, lon), (end_lat, end_lon) in zip(points, points[1:]):
        east = (end_lon - lon) * math.cos(math.radians((end_lat + lat) / 2))
        self.lengths.append(self.lengths[-1] + 111320 * math.hypot(end_lat - lat, east))
    steps = route['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 10000:
      raise ValueError('Invalid maneuvers')
    self.turns = []
    previous = -1
    for index, instruction in steps:
      if type(index) is not int or not previous <= index < len(self.points) or index < 0:
        raise ValueError('Invalid maneuver position')
      if not isinstance(instruction, str) or not 1 <= len(instruction) <= 400 or not instruction.isprintable():
        raise ValueError('Invalid instruction')
      self.turns.append((self.lengths[index], instruction))
      previous = index
    if previous != len(self.points) - 1:
      raise ValueError('Missing arrival')
    self.distances = [d for d, _ in self.turns]

  def cue_at(self, lat, lon):
    east = 111320 * math.cos(math.radians(lat))
    best, along = 30.0, None
    for start, south, north, west, right in self.blocks:
      if not (south - .0003 <= lat <= north + .0003 and west - .0006 <= lon <= right + .0006):
        continue
      for i in range(start, min(start + self.block_size, len(self.points) - 1)):
        a, b = self.points[i:i + 2]
        x, y = (lon - a[1]) * east, (lat - a[0]) * 111320
        dx, dy = (b[1] - a[1]) * east, (b[0] - a[0]) * 111320
        squared = dx * dx + dy * dy
        t = max(0., min(1., (x * dx + y * dy) / squared)) if squared else 0.
        offset = math.hypot(x - t * dx, y - t * dy)
        if offset <= best:
          best = offset
          along = self.lengths[i] + t * (self.lengths[i + 1] - self.lengths[i])
    if along is None:
      return 'Outside planned route'
    last_lat, last_lon = self.points[-1]
    if self.lengths[-1] - along <= 10 and math.hypot((last_lat-lat)*111320, (last_lon-lon)*east) <= 12:
      return 'At destination'
    idx = min(bisect_left(self.distances, along), len(self.turns) - 1)
    distance, instruction = self.turns[idx]
    remaining = max(0., distance - along)
    label = f'{remaining / 1000:.1f} km' if remaining >= 1000 else f'{round(remaining)} m'
    return f'{label}  {instruction}'


def parse_route(route):
  return NationalRoute(route) if route.get('format') == 'au-route-1' else RouteCue(route)


class RouteDisplay:
  def __init__(self, path=ROUTE_FILE):
    self.path = path
    self.route = None
    self.frame = None
    self.cue = ''
    self._file_version = None
    self._next_check = 0.0

  def _refresh_route(self, now):
    if now < self._next_check:
      return
    self._next_check = now + 1.0
    try:
      version = route_version(self.path)
    except OSError:
      version = None
    if version == self._file_version:
      return

    self.route = RouteCue.load(self.path) if version is not None else None
    self._file_version = version
    if self.path == ROUTE_FILE and self.route is not None:
      try:
        DISPLAY_ACK.write_text(version)
      except OSError:
        pass
    self.frame = None
    self.cue = ''

  def parked_preview(self):
    self._refresh_route(monotonic())
    if self.route is None:
      return ''
    return f'To {self.route.destination}'

  def current_text(self, sm, started_frame, now):
    self._refresh_route(now)
    if self.route is None:
      return ''
    service = 'gpsLocationExternal'
    frame = sm.recv_frame[service]
    if frame < started_frame or not sm.valid[service] or not sm.alive[service] or not 0 <= now - sm.recv_time[service] <= 2:
      return ''
    fix = sm[service]
    if not fix.hasFix or not 0 < fix.horizontalAccuracy <= 15:
      return ''
    if self.frame != frame:
      self.frame = frame
      self.cue = self.route.cue_at(fix.latitude, fix.longitude)
    return self.cue
