/**
 * Build the API using tools in core.js
 */

import { Api } from "./core.js"

const api = new Api("/api")
api.addGeneralModule("parent")

// Store the instance on the window for access elsewhere
window.api = api
