#
# Copyright 2014 Mimetic Markets, Inc.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#

from collections import defaultdict
import inspect
from twisted.internet.defer import inlineCallbacks, returnValue, DeferredList

import observatory

debug, log, warn, error, critical = observatory.get_loggers("plugin_manager")

class PluginException(Exception):
    pass

class PluginManager:
    def __init__(self):
        self.plugins = {}
        self.services = defaultdict(list)
        self.events = defaultdict(list)

    def register(self, plugin, event, handler):
        if not callable(handler):
            raise PluginException("Handler %s is not callable." % handler)
        self.events[event].append((plugin, handler))

    def unregister(self, plugin, event, handler):
        for i in range(len(self.events[event])):
            p, h = self.events[event][i]
            if plugin == p and handler == h:
                del self.events[event][i]
                break
        else:
            raise PluginException("Handler %s not registered for event %s." % \
                    (handler, event))

    def emit(self, plugin, event, *args, **kwargs):
        for p, h in self.events[event]:
            try:
                h(*args, **kwargs)
            except Exception as e:
                error("Caught exception handling event %s in %s." % \
                        (event, p.plugin_path))
                error(e)

    def load(self, path):
        module_name, class_name = path.rsplit(".", 1)
        mod = __import__(module_name)
        for component in module_name.split(".")[1:]:
            mod = getattr(mod, component)
        klass = getattr(mod, class_name)
        plugin = klass()
        plugin.module_name = module_name
        plugin.service_name = module_name.rsplit(".", 1)[0]
        plugin.class_name = class_name
        plugin.plugin_path = path
        plugin.handlers = defaultdict(list)
        plugin.manager = self
        return self._load_plugin(plugin)

    @inlineCallbacks
    def init(self, plugin):
        # register on_ handlers
        for name, method in inspect.getmembers(plugin, inspect.ismethod):
            if name.startswith("on_"):
                plugin.on(name.split("_", 1)[1], method)

        plugin = yield self._init_plugin(plugin)
        returnValue(plugin)

    @inlineCallbacks
    def shutdown(self, plugin):
        # unregister handlers
        for event in self.events:
            self.events[event] = filter(lambda (p, h): p != plugin,
                    self.events[event])
        plugin = yield self._shutdown_plugin(plugin)
        returnValue(plugin)

    def unload(self, path):
        return self._unload_plugin(self.plugins[path])

    def _load_plugin(self, plugin):
        path = plugin.plugin_path
        debug("Loading plugin %s..." % path)
        if path in self.plugins:
            warn("Plugin %s already loaded." % path)
            return
        debug("Configuring plugin %s..." % path)
        plugin.configure()
        debug("Plugin %s loaded." % path)
        self.plugins[path] = plugin
        self.services[plugin.service_name].append(plugin)
        return plugin

    @inlineCallbacks
    def _init_plugin(self, plugin):
        path = plugin.plugin_path
        debug("Initializing plugin %s..." % path)
        try:
            yield plugin.init()
        except Exception, e:
            error("Unable to initialize plugin %s." % path)
            error(e)
            raise PluginException("Unable to initialize plugin %s." % path)
        debug("Plugin %s is ready." % path)
        returnValue(plugin)

    @inlineCallbacks
    def _shutdown_plugin(self, plugin):
        path = plugin.plugin_path
        debug("Shutting down plugin %s..." % path)
        try:
            yield plugin.shutdown()
        except Exception, e:
            error("Unable to shut down plugin %s." % path)
            error(e)
            raise PluginException("Unable to shut down plugin %s." % path)
        debug("Plugin %s is done." % path)
        returnValue(plugin)

    def _unload_plugin(self, plugin):
        path = plugin.plugin_path
        debug("Unloading plugin %s..." % path)
        if path not in self.plugins:
            warn("Plugin %s not loaded." % path)
            return
        del self.plugins[path]
        if plugin in self.services[plugin.service_name]:
            self.services[plugin.service_name].remove(plugin)
        debug("Plugin %s unloaded." % path)

class Plugin:
    def __init__(self):
        pass

    def configure(self):
        pass

    def emit(self, event, *args, **kwargs):
        self.manager.emit(self, event, *args, **kwargs)

    def on(self, event, handler):
        self.manager.register(self, event, handler)

    def off(self, event, handler):
        self.manager.unregister(self, event, handler)

    def init(self):
        pass

    def shutdown(self):
        pass

    def require(self, path):
        plugin = self.manager.plugins.get(path)
        if not plugin:
            raise PluginException("Plugin %s requires %s." % \
                    (self.plugin_path, path))
        return plugin

def run_with_plugins(reactor, plugin_paths, callback, *args, **kwargs):
    plugin_manager = PluginManager()
    plugins = [plugin_manager.load(plugin_path) for plugin_path in plugin_paths]

    @inlineCallbacks
    def init_and_run():
        for plugin in plugins:
            try:
                result = yield plugin_manager.init(plugin)
            except Exception as e:
                error("Not all plugins initialized. Aborting for plugin: %s." % plugin)
                return
        callback(plugin_manager, *args, **kwargs)

    @inlineCallbacks
    def cleanup():
        plugins.reverse()
        plugin_paths.reverse()
        for plugin in plugins:
            try:
                yield plugin_manager.shutdown(plugin)
            except Exception, e:
                pass
        for plugin_path in plugin_paths:
            plugin_manager.unload(plugin_path)

    reactor.callWhenRunning(init_and_run)
    reactor.addSystemEventTrigger('before', 'shutdown', cleanup)
    reactor.run()

