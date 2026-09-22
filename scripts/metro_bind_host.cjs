'use strict';

// Expo CLI versions used by the local demo may advertise --host localhost
// without passing a host to Node's Metro listener.  The launcher loads this
// shim only for the disposable Metro child and supplies a validated bind host.
// Limit the interception to that exact port so unrelated Node listeners are
// untouched.
const net = require('node:net');

const bindHost = process.env.NEXA_METRO_BIND_HOST;
const bindPort = Number(process.env.NEXA_METRO_BIND_PORT);
const originalListen = net.Server.prototype.listen;

if (
  bindHost &&
  Number.isInteger(bindPort) &&
  bindPort >= 1 &&
  bindPort <= 65535 &&
  !net.Server.prototype.__nexaMetroBindHostPatched
) {
  const listen = function patchedListen(...args) {
    const first = args[0];
    const isTargetPort =
      (typeof first === 'number' && first === bindPort) ||
      (first && typeof first === 'object' && first.port === bindPort);

    if (!isTargetPort) {
      return originalListen.apply(this, args);
    }

    const patchedArgs = [...args];
    if (first && typeof first === 'object') {
      patchedArgs[0] = { ...first, host: bindHost };
    } else if (patchedArgs.length >= 2 && typeof patchedArgs[1] !== 'function') {
      patchedArgs[1] = bindHost;
    } else {
      patchedArgs.splice(1, 0, bindHost);
    }

    return originalListen.apply(this, patchedArgs);
  };

  Object.defineProperty(net.Server.prototype, '__nexaMetroBindHostPatched', {
    configurable: false,
    enumerable: false,
    value: true,
    writable: false,
  });
  net.Server.prototype.listen = listen;
}
