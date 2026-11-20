"""Build the offline corridor from a Geofabrik Victoria OSM PBF (requires osmium)."""
import gzip
import json
import math
import sys
from pathlib import Path

import osmium

ROOT = Path(__file__).resolve().parent
ORIGIN = (144.92, -37.71)
SCALE = 111320
EAST = SCALE * math.cos(math.radians(ORIGIN[1]))
BOX = (144.895, -37.738, 144.957, -37.681)
ROAD_TYPES = {'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street',
              'primary_link', 'secondary_link', 'tertiary_link', 'trunk', 'trunk_link'}


def xy(lon, lat):
  return [round((lon - ORIGIN[0]) * EAST, 1), round((lat - ORIGIN[1]) * SCALE, 1)]


def build(pbf):
  coords = {}
  blocked = set()
  for n in osmium.FileProcessor(pbf, osmium.osm.NODE):
    lon, lat = n.location.lon, n.location.lat
    if BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]:
      coords[n.id] = xy(lon, lat)
      if n.tags.get('barrier') not in (None, 'no', 'toll_booth', 'cattle_grid') or n.tags.get('motor_vehicle') == 'no':
        blocked.add(n.id)

  roads = []
  for w in osmium.FileProcessor(pbf, osmium.osm.WAY).with_filter(osmium.filter.KeyFilter('highway')):
    t = w.tags
    if t.get('highway') not in ROAD_TYPES or w.nodes[0].ref not in coords:
      continue
    ids = [n.ref for n in w.nodes]
    if not all(n in coords and n not in blocked for n in ids):
      continue
    access = next((t[k] for k in ('motorcar', 'motor_vehicle', 'vehicle', 'access') if k in t), 'yes')
    if access not in ('yes', 'permissive', 'designated') or any(tag.k in ('access:conditional', 'vehicle:conditional', 'motor_vehicle:conditional', 'motorcar:conditional', 'oneway:conditional') for tag in t):
      continue
    circle = t.get('junction') in ('roundabout', 'circular')
    direction = t.get('oneway', 'yes' if circle else 'no')
    if direction not in ('yes', '1', 'true', '-1', 'no', 'false', '0'):
      continue
    roads.append([w.id, t.get('name') or t.get('ref') or 'unnamed road', ids,
                  -1 if direction == '-1' else int(direction in ('yes', 'true', '1')), circle])

  ways = {r[0] for r in roads}
  excluded = set()
  rules = []
  for r in osmium.FileProcessor(pbf, osmium.osm.RELATION).with_filter(osmium.filter.TagFilter(('type', 'restriction'))):
    origins = [m.ref for m in r.members if m.role == 'from' and m.type == 'w' and m.ref in ways]
    if not origins:
      continue
    rule = r.tags.get('restriction:motorcar') or r.tags.get('restriction:motor_vehicle') or r.tags.get('restriction')
    if not rule and not any('conditional' in tag.k for tag in r.tags):
      continue
    vias = [m.ref for m in r.members if m.role == 'via' and m.type == 'n']
    ends = [m.ref for m in r.members if m.role == 'to' and m.type == 'w']
    if len(vias) != 1 or not rule or not ends or any('conditional' in tag.k for tag in r.tags):
      excluded.update(origins)
      continue
    for f in origins:
      for end in ends:
        rules.append([f, vias[0], end, rule])

  roads = [r for r in roads if r[0] not in excluded]
  used = {n for road in roads for n in road[2]}
  addresses = {}
  for file, suburb in [('map.js', 'Jacana'), ('pascoe.js', 'Pascoe Vale')]:
    local = json.loads((ROOT / file).read_text().split('=', 1)[1].strip().rstrip(';'))
    lon, lat = local['origin']
    for name, (x, y) in local['addresses'].items():
      addresses[f'{name}, {suburb}'] = xy(lon + x / (SCALE * math.cos(math.radians(lat))), lat + y / SCALE)
  data = dict(origin=ORIGIN, bounds=[xy(*BOX[:2]), xy(*BOX[2:])], roads=roads, nodes={n: coords[n] for n in sorted(used)},
              rules=rules, addresses=addresses)
  raw = ('const data = ' + json.dumps(data, separators=(',', ':')) + ';\n').encode()
  (ROOT / 'corridor.js.gz').write_bytes(gzip.compress(raw, mtime=0))


if __name__ == '__main__':
  build(sys.argv[1])
