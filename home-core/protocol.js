'use strict';

const PROTOCOL_VERSION = 1;
const CORE_VERSION = '0.1.0';
const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 17777;
const HEALTH_PATH = '/v1/health';
const CAPABILITIES_PATH = '/v1/capabilities';
const EVENTS_PATH = '/v1/events';
const SHUTDOWN_PATH = '/v1/system/shutdown';

module.exports = { PROTOCOL_VERSION, CORE_VERSION, DEFAULT_HOST, DEFAULT_PORT, HEALTH_PATH, CAPABILITIES_PATH, EVENTS_PATH, SHUTDOWN_PATH };
