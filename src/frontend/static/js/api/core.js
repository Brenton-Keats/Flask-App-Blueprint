/**
 * Core classes used by all API instances.
 * 
 * Author: Brenton Keats, 2023
 */

export { _ApiCore, Api }

/**
 * Base class for implementing API interactions.
 */
class _ApiCore {
    #session;

    /**
     * Set up the core functionality of this API module.
     * @param {String} path Root URL for all enclosed endpoints.
     * @param {_SessionManager} sessionManager _SessionManager instance to use for session. 
     */
    constructor (path, sessionManager=null) {
        this.path = path
        this.actions = {"custom": `${path}/...`}
        this.#session = sessionManager
    }

    _getSessionManager() {
        return this.#session
    }

    /**
     * Separate query-based args from model-based args.
     * @param {Object} args Collection of all args to be passed in a request.
     * @returns {Object} queryArgs and modelArgs respectively.
     */
    #sortArgs(args) {
        const queryArgKeys = ["_page", "_pagelength", "_query", "_sortby", "_session"]
        let queryArgs = {}
        let modelArgs = {}
        for (const [key, value] of Object.entries(args)) {
            (queryArgKeys.includes(key)
                ? queryArgs
                : modelArgs
            )[key] = value
        }
        return { queryArgs, modelArgs }
    }

    /**
     * Emit a HTTP request with parameters.
     * @param {String} url URL slug to send request to.
     * @param {String} method HTTP method to use.
     * @param {Object} queryArgs Arguments to pass in the URL query string.
     * @param {Object} bodyArgs Arguments to pass in the request body, if method !== "GET"
     * @returns {Response} HTTP Response object.
     */
    async _basicFetch (url, method="GET", queryArgs={}, bodyArgs={}) {
        if (queryArgs && Object.keys(queryArgs).length) {
            url = url + (url.includes("?") ? "&" : "?") + (new URLSearchParams(queryArgs)).toString()
        }
        let options = {
            method: method,
            headers: {
                "Content-Type": "application/json",
            },
        }
        if (method !== "GET") {
            options.body = JSON.stringify(bodyArgs)
        }
        return await fetch(url, options)
    }

    /**
     * Emit a HTTP request enclosed in a specific DB session. Expect JSON response.
     * @param {String} url URL slug to send request to.
     * @param {String} method HTTP method to use.
     * @param {Object} args All args to be sent in the request (query string + body).
     * @param {String} session DB session ID to use. Optional.
     * @returns {Object} JSON response from API.
     */
    async _fetchResponse(url, method, args={}, session=null) {

        if (args._session) {
            throw new Error("_session should not be added to args directly!")
        }
        const sessionIsTemporary = !Boolean(session)
        if (!session) {
            let sessionResponse = await this.#session.get()
            session = sessionResponse.result.session_id
        }
        args._session = session

        let response;
        if (method === 'GET') {
            response = this._basicFetch(url, method, args)
        } else {
            const { queryArgs, modelArgs } = this.#sortArgs(args)
            response = this._basicFetch(url, method, queryArgs, modelArgs)
        }

        let data = await response.then(async response => {    
            const contentType = response.headers.get("content-type");
            if (contentType && contentType.indexOf("application/json") === -1) {
                throw new Error("Unexpected Content-Type in response from API: " + contentType)
            }
            return response.json()
        })

        if (data.success) {
            if (sessionIsTemporary) {
                this.#session.save(session)
            }
        } else {
            if (sessionIsTemporary) {
                this.#session.rollback(session, true)
            }
        }
        return data
    }

    /**
     * Emit a HTTP request to a custom endpoint.
     * @param {String} urlPath Remaining URL slug to send request to. Appended to `this.path`
     * @param {String} session DB session ID to use. Optional.
     * @param {Object} args All arguments to pass in request (query string + body)
     * @param {String} method HTTP method to use.
     * @returns {Promise} Resolves to JSON response from API.
     */
    custom(urlPath, args={}, session=null, method='GET') {
        const url = this.path + urlPath
        return this._fetchResponse(url, method, args, session)
    }

    /**
     * Add a resource to this API module.
     * @param {String} name Valid attribute name to register this resource under. Must not already be registered.
     * @param {CallableFunction | _ApiCore} resource Callable method, or API submodule.
     */
    _addResource(name, resource) {
        if (name.includes(" ")) {
            throw new Error(`Passed value "${moduleName}" is not valid`)
        }
        if (this[name]) {
            throw new Error(`An attribute with name "${name}" already exists for this API module.`)
        }
        this[name] = resource
    }

    /**
     * Register a submodule.
     * @param {String} name Valid attribute name to register this module under. Must not already be registered.
     * @param {_ApiCore} newModule Object with methods.
     */
    addCustomModule(name, newModule) {
        if (!(
                newModule.prototype instanceof _ApiCore  // Is subclass of...
                || newModule instanceof _ApiCore         // Is instance of...
            )) {
            throw new TypeError("Module must extend _ApiCore")
        }
        this._addResource(name, newModule)
        this.actions[name] = `${newModule.path}/...`
    }

    /**
     * Register a single endpoint on this module.
     * @param {String} name Valid attribute name to register this endpoint under. Must not already be registered.
     * @param {String} path Remaining URL slug to use. Optional, defaults to `/${name}`
     * @param {String} method HTTP method to use.
     */
    addEndpoint(name, path=null, method="GET") {
        if (!path) {
            path = "/" + name
        }
        const url = this.path + path
        const parent = this
        /**
         * Emit a HTTP request to this endpoint.
         * @param {Object} args Parameters to pass to the endpoint.
         * @param {String} session DB session ID to use. Optional.
         * @returns {Promise} Resolves to JSON response from API.
         */
        function ApiEndpoint(args={}, session=null) {
            return parent._fetchResponse(url, method, args, session)
        }

        this._addResource(name, ApiEndpoint)
        this.actions[name] = url
    }
}

/**
 * Class representing a collection of API endpoints.
 * @extends _ApiCore
 */
class ApiGeneralCollection extends _ApiCore {
    /**
     * Declare a generic collection of API endpoints. This contains methods for CRUD and bulk-read actions.
     * @param {String} path Root URL for all enclosed endpoints.
     * @param {_SessionManager} sessionManager _SessionManager instance to use for session. 
     * @param {Number} defaultPageLength Default page size for any paginated queries.
     */
    constructor (path, sessionManager, defaultPageLength=100) {
        super(path, sessionManager)
        this.defaultPageLength = defaultPageLength
        this.actions = {
            ...this.actions,
            "listIds": `${this.path}/`,
            "listDetails": `${this.path}/details`,
            "create": `${this.path}/`,
            "read": `${this.path}/{id}`,
            "update": `${this.path}/{id}`,
            "delete": `${this.path}/{id}`,
        }
    }

    /**
     * Fetch a list of IDs for this collection.
     * @param {Object} args Model attribute values to require i.e. `{mod_val: 3}`.
     * @param {String} session DB session ID to use. Optional.
     * @param {Number} page Page number to request. Defaults to 1.
     * @param {Number} pageLength Number of records per page. Defaults to `this.defaultPageLength`.
     * @param {String} matchText Substring to match on any model attribute (coerced to string).
     * @param {String} sortBy Model attribute to sort the results by.
     * @returns {Promise} Resolves to JSON response from API.
     */
    listIds(args, session=null, page=1, pageLength=this.defaultPageLength, matchText, sortBy="id") {
        const url = this.path + "/"
        let queryArgs = {
                _page: page,
                _pagelength: pageLength,
                _sortby: sortBy,
        }
        if (matchText) {
            queryArgs._query = matchText
        }
        return this._fetchResponse(
            url,
            'GET',
            {
                ...args,
                ...queryArgs
            },
            session
        )
    }

    /**
     * Fetch a list of IDs for this collection.
     * @param {Object} args Model attribute values to require i.e. `{mod_val: 3}`.
     * @param {String} session DB session ID to use. Optional.
     * @param {Number} page Page number to request. Defaults to 1.
     * @param {Number} pageLength Number of records per page. Defaults to `this.defaultPageLength`.
     * @param {String} matchText Substring to match on any model attribute (coerced to string).
     * @param {String} sortBy Model attribute to sort the results by.
     * @returns {Promise} Resolves to JSON response from API.
     */
    listDetails(args, session=null, page=1, pageLength=this.defaultPageLength, matchText, sortBy="id") {
        const url = this.path + '/details'
        let queryArgs = {
                _page: page,
                _pagelength: pageLength,
                _sortby: sortBy,
        }
        if (matchText) {
            queryArgs._query = matchText
        }
        return this._fetchResponse(
            url,
            'GET',
            {
                ...args,
                ...queryArgs
            },
            session
        )
    }

    /**
     * Create a new record in this collection.
     * @param {Object} args Model attribute values to set. Those not declared will default according to the server.
     * @param {String} session DB session ID to use. Optional.
     * @returns {Promise} Resolves to JSON response from API.
     */
    create(args, session=null) {
        const url = this.path + "/"
        return this._fetchResponse(url, 'POST', args, session)
    }

    /**
     * Read an existing record from this collection.
     * @param {Number} id Database ID of the record to request.
     * @param {String} session DB session ID to use. Optional.
     * @returns {Promise} Resolves to JSON response from API.
     */
    read(id, session=null) {
        if (!id) {
            throw new Error('ID required')
        }
        const url = this.path + `/${id}`
        return this._fetchResponse(url, 'GET', {}, session)
    }

    /**
     * Update an existing record in this collection.
     * @param {Number} id Database ID of the record to update.
     * @param {Object} args Model attribute values to set. Those not declared will default according to the server.
     * @param {String} session DB session ID to use. Optional.
     * @returns {Promise} Resolves to JSON response from API.
     */
    update(id, args, session=null) {
        const url = this.path + `/${id}`
        return this._fetchResponse(url, 'POST', args, session)
    }

    /**
     * Delete a record from this collection.
     * @param {Number} id Database ID of the record to update.
     * @param {String} session DB session ID to use. Optional.
     * @returns {Promise} Resolves to JSON response from API.
     */
    delete(id, session) {
        const url = this.path + `/${id}`
        return this._fetchResponse(url, 'DELETE', session)
    }
}

/**
 * Class representing a complete API.
 */
class Api extends _ApiCore {
    /**
     * Declare an API and its modules.
     * @param {String} path Base URL for all API endpoints.
     */
    constructor(path="/api") {
        let session = new _SessionManager(path + "/session")
        super(path, session)
        this.session = session

    }

    /**
     * Register a generic collection of API endpoints.
     * @param {String} moduleName Valid attribute name to save this module under. Must not already be registered on this API.
     * @param {String} urlPath Remaining URL slug to append to `this.path`.
     */
    addGeneralModule(moduleName, urlPath=null) {
        if (!urlPath) {
            urlPath = "/" + moduleName
        };
        urlPath = this.path + encodeURI(urlPath);
        const newModule = new ApiGeneralCollection(urlPath, this._getSessionManager());
        this.addCustomModule(moduleName, newModule);
    }
}

/**
 * Class containing database session management methods.
 */
class _SessionManager extends _ApiCore {
    /**
     * Declare session management methods.
     * @param {String} path Base URL for all API endpoints.
     */
    constructor (path) {
        super(path)
        this.actions = {
            ...this.actions,
            "get": `${this.path}/new`,
            "save": `${this.path}/save/{session}`,
            "rollback": `${this.path}/rollback/{session}`,
        }
    }

    /**
     * Start a new DB session.
     * @returns {Object} API response outlining a new DB session.
     */
    async get() {
        const url = this.path + "/new"
        return await this._basicFetch(url, 'GET').then(r => r.json())
    }

    /**
     * Save (commit) all changes in an existing DB session. Closes the session.
     * @param {String} session ID of DB session to rollback.
     * @returns {Object} API response outlining DB session closure.
     */
    async save(session) {
        const url = this.path + "/save/" + session
        return await this._basicFetch(url, 'GET').then(r => r.json())
    }

    /**
     * Rollback all changes in an existing DB session. Optionally closes the session.
     * @param {String} session ID of DB session to rollback.
     * @param {boolean} close Whether or not to close the session.
     * @returns {Object} API response outlining DB session closure.
     */
    async rollback(session, close=true) {
        const closeStr = close ? "y" : "n"
        const url = this.path + "/rollback/" + session + "?" + (new URLSearchParams({"close": closeStr})).toString()
        return await this._basicFetch(url, 'GET').then(r => r.json())
    }
}
