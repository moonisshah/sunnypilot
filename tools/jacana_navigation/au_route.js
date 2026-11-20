// Convert OSRM's step geometry to one polyline6 with maneuver point indices.
function auRoute(response, destination) {
  const route = response.routes?.[0], leg = route?.legs?.[0];
  if (response.code !== 'Ok' || route?.legs?.length !== 1 || !leg?.steps?.length)
    throw Error('No connected driving route was returned.');
  const parts = [], steps = [];
  let lastLat = 0, lastLon = 0, count = 0;
  function appendDelta(delta) {
    let value = delta < 0 ? -2 * delta - 1 : 2 * delta;
    while (value >= 32) {
      parts.push(String.fromCharCode(95 + value % 32));
      value = Math.floor(value / 32);
    }
    parts.push(String.fromCharCode(63 + value));
  }
  for (const step of leg.steps) {
    let begin;
    for (const [lon, lat] of step.geometry.coordinates) {
      const y = Math.round(lat * 1e6), x = Math.round(lon * 1e6);
      if (!Number.isFinite(x) || !Number.isFinite(y)) throw Error('Invalid route geometry.');
      if (!count || y !== lastLat || x !== lastLon) {
        appendDelta(y - lastLat);
        appendDelta(x - lastLon);
        lastLat = y;
        lastLon = x;
        count++;
      }
      if (begin === undefined) begin = count - 1;
    }
    if (begin === undefined) throw Error('Missing route geometry.');
    const m = step.maneuver, dir = m.modifier || '', road = step.name || step.ref;
    const onto = road ? ' onto ' + road : '';
    let cue;
    if (m.type === 'arrive') cue = 'Reach destination';
    else if (step.mode === 'ferry') cue = 'Take the ferry' + (road ? ' · ' + road : '');
    else if (['roundabout','rotary','roundabout turn'].includes(m.type))
      cue = 'At the roundabout, ' + (m.exit ? 'take exit ' + m.exit : 'continue ' + dir) + onto;
    else if (m.type.startsWith('exit ')) cue = 'Exit the roundabout' + onto;
    else if (dir === 'uturn') cue = 'Make a U-turn' + onto;
    else if (m.type === 'on ramp' || m.type === 'off ramp') cue = 'Take the ramp ' + dir + onto;
    else if (m.type === 'merge') cue = 'Merge ' + dir + onto;
    else if (m.type === 'fork') cue = 'Keep ' + dir + onto;
    else if (m.type === 'turn' || m.type === 'end of road') cue = 'Turn ' + dir + onto;
    else cue = 'Continue ' + (m.type === 'depart' ? '' : dir) + (road ? ' on ' + road : '');
    steps.push([begin,cue.replace(/\s+/g,' ').trim().slice(0,400)]);
  }
  if (count < 2 || count > 150000 || steps.length > 10000 || steps.at(-1)[0] !== count - 1)
    throw Error('This route exceeds the display limits. Choose an intermediate destination.');
  return {format:'au-route-1',destination:destination.slice(0,120),shape:parts.join(''),steps};
}
