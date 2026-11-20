"""Background recalculation for opted-in Australian driving directions."""
import json
from urllib.request import Request, urlopen

from route_cue import MAX_ROUTE_BYTES, NationalRoute

OFF_ROUTE = 'Off route'


def instruction(step):
  maneuver = step['maneuver']
  kind, direction = maneuver['type'], maneuver.get('modifier', '')
  road = step.get('name') or step.get('ref')
  onto = f' onto {road}' if road else ''
  if kind == 'arrive':
    return 'Reach destination'
  if step.get('mode') == 'ferry':
    return 'Take the ferry' + (f' · {road}' if road else '')
  if kind in ('roundabout', 'rotary', 'roundabout turn'):
    return 'At the roundabout, ' + (f"take exit {maneuver['exit']}" if 'exit' in maneuver else f'continue {direction}') + onto
  if kind.startswith('exit '):
    return 'Exit the roundabout' + onto
  if direction == 'uturn':
    return 'Make a U-turn' + onto
  verb = {'on ramp': 'Take the ramp', 'off ramp': 'Take the ramp', 'fork': 'Keep',
          'merge': 'Merge', 'turn': 'Turn', 'end of road': 'Turn'}.get(kind, 'Continue')
  return ' '.join(f'{verb} {direction if kind != "depart" else ""}{onto}'.split())


def encode_delta(delta):
  value = ~(delta << 1) if delta < 0 else delta << 1
  parts = []
  while value >= 32:
    parts.append(chr(95 + (value & 31)))
    value >>= 5
  return ''.join(parts) + chr(63 + value)


def replacement(position, target, destination):
  lat, lon = position
  end_lat, end_lon = target
  url = (f'https://router.project-osrm.org/route/v1/driving/{lon:.6f},{lat:.6f};{end_lon:.6f},{end_lat:.6f}'
         '?steps=true&geometries=geojson&overview=false&radiuses=50;50')
  with urlopen(Request(url, headers={'User-Agent': 'SantaFeNavigation/1.0'}), timeout=15) as response:
    raw = response.read(16_000_001)
  if len(raw) > 16_000_000:
    raise ValueError('Route response too large')
  data = json.loads(raw)
  if data.get('code') != 'Ok':
    raise ValueError('Routing unavailable')
  shape, steps = [], []
  previous = None
  count = 0
  for step in data['routes'][0]['legs'][0]['steps']:
    begin = None
    for x, y in step['geometry']['coordinates']:
      point = round(y * 1e6), round(x * 1e6)
      if point != previous:
        a, b = previous or (0, 0)
        shape.append(encode_delta(point[0] - a) + encode_delta(point[1] - b))
        previous = point
        count += 1
      if begin is None:
        begin = count - 1
    steps.append([begin, instruction(step)[:400]])
  payload = dict(format='au-route-1', destination=destination, shape=''.join(shape), steps=steps, autoReroute=True)
  body = json.dumps(payload, separators=(',', ':')).encode()
  if len(body) > MAX_ROUTE_BYTES:
    raise ValueError('Route exceeds display limit')
  return body, NationalRoute(payload)


class Rerouter:
  def __init__(self, submit):
    self.submit = submit
    self.pending = None
    self.reset()

  def reset(self):
    self.armed = False
    self.arrived = False
    self.next_try = 0.
    self.last_stamp = None
    self.last_time = None
    self.off_since = None
    self.generation = getattr(self, 'generation', 0) + 1

  def tick(self, route, stamp, position, now):
    if route is None or not route.auto_reroute or self.arrived:
      return
    if position is None or (self.last_time is not None and now - self.last_time > 2):
      self.off_since = None
      self.generation += 1
    if position is None or stamp == self.last_stamp:
      return
    self.last_time, self.last_stamp = now, stamp
    cue = route.cue_at(*position)
    if cue != OFF_ROUTE:
      self.armed = True
      self.arrived = cue == 'At destination'
      self.off_since = None
      self.generation += 1
      return
    if not self.armed:
      return
    if self.off_since is None:
      self.off_since = now
    if self.pending is not None:
      future, generation = self.pending
      if not future.done():
        return
      self.pending = None
      try:
        body, candidate = future.result()
      except Exception:
        self.next_try = now + 30
        return
      if generation == self.generation and candidate.cue_at(*position) != OFF_ROUTE:
        return body
    if now - self.off_since >= 4 and now >= self.next_try:
      self.pending = self.submit(replacement, position, route.points[-1], route.destination), self.generation
      self.next_try = now + 30
