import io
import json
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import serve
from reroute import encode_delta

START = (-35., 149.)
END = (-35., 149.01)
MISSED = (-34.999, 149.005)


def step(kind, points):
  return {'maneuver': {'type': kind}, 'name': 'Example Road',
          'geometry': {'coordinates': [[lon, lat] for lat, lon in points]}}


class RerouteTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.path = Path(temporary.name) / 'route'
    self.patch('serve.ROUTE_FILE', self.path)
    self.patch('serve.RouteCue.load', lambda: serve.parse_route(json.loads(self.path.read_bytes())))
    save = serve.save_route
    self.patch('serve.save_route', lambda body: save(body, self.path))
    self.route = dict(format='au-route-1', destination='Original', autoReroute=True,
                      steps=[[1, 'Reach destination']], shape=''.join(map(encode_delta, [-35000000,149000000,0,10000])))
    self.send()
    self.server = object.__new__(serve.PlannerServer)
    self.server.next_check = 0
    self.server.last_fix = self.server.watched_route = self.server.watched_version = None
    self.server.rerouter = serve.Rerouter(self.submit)
    self.requests = []
    self.clock = 50

  def patch(self, name, value):
    patcher = patch(name, value)
    patcher.start()
    self.addCleanup(patcher.stop)

  def send(self):
    serve.save_route(json.dumps(self.route).encode())

  def submit(self, fn, *args):
    self.requests.append((fn, args, Future()))
    return self.requests[-1][-1]

  def fix(self, pos, valid=True):
    self.clock += 1
    stamp = self.clock * 10**9
    event = SimpleNamespace(valid=valid, logMonoTime=stamp, gpsLocationExternal=SimpleNamespace(
      hasFix=True, horizontalAccuracy=4, latitude=pos[0], longitude=pos[1]))
    self.server.read_gps = lambda: event
    with patch('serve.monotonic', return_value=self.clock), patch('serve.monotonic_ns', return_value=stamp):
      self.server.service_actions()

  def miss(self):
    self.fix(START)
    for _ in range(5):
      self.fix(MISSED)
    self.assertEqual(len(self.requests), 1)

  def finish(self):
    fn, args, future = self.requests[0]
    data = {'code': 'Ok', 'routes': [{'legs': [{'steps': [step('depart', [MISSED,END]), step('arrive', [END])]}]}]}
    with patch('reroute.urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
      future.set_result(fn(*args))

  def test_replacement_and_arrival(self):
    self.miss()
    self.finish()
    self.fix(MISSED)
    route = serve.RouteCue.load()
    self.assertEqual(route.destination, 'Original')
    self.assertEqual(route.points, [MISSED, END])
    self.fix(END)
    for _ in range(10):
      self.fix(START)
    self.assertEqual(len(self.requests), 1)

  def test_cancel_during_request(self):
    self.miss()
    self.path.unlink()
    self.finish()
    self.fix(MISSED)
    self.assertFalse(self.path.exists())

  def test_new_destination_during_request(self):
    self.miss()
    self.route['destination'] = 'New'
    self.send()
    self.finish()
    self.fix(MISSED)
    self.assertEqual(serve.RouteCue.load().destination, 'New')

  def test_gps_gap_and_network_retry(self):
    self.fix(START)
    for _ in range(4):
      self.fix(MISSED)
    self.fix(MISSED, valid=False)
    for _ in range(4):
      self.fix(MISSED)
    self.assertFalse(self.requests)
    self.fix(MISSED)
    self.requests[0][-1].set_exception(OSError('offline'))
    for _ in range(30):
      self.fix(MISSED)
    self.assertEqual(len(self.requests), 1)
    self.fix(MISSED)
    self.assertEqual(len(self.requests), 2)

  def test_rejoin_before_response(self):
    self.miss()
    self.fix(START)
    self.finish()
    self.fix(MISSED)
    self.assertEqual(serve.RouteCue.load().points, [START,END])

  def test_existing_trip_without_opt_in(self):
    del self.route['autoReroute']
    self.send()
    self.fix(START)
    for _ in range(10):
      self.fix(MISSED)
    self.assertFalse(self.requests)
