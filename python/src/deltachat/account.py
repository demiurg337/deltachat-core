from __future__ import print_function
import threading
import requests
from . import capi
import deltachat
from .capi import ffi


class Account:
    def __init__(self, db_path, logcallback=None):
        self.dc_context = ctx = capi.lib.dc_context_new(
                                  capi.lib.py_dc_callback,
                                  capi.ffi.NULL, capi.ffi.NULL)
        capi.lib.dc_open(ctx, db_path, capi.ffi.NULL)
        self._logcallback = logcallback

    def set_config(self, **kwargs):
        for name, value in kwargs.items():
            capi.lib.dc_set_config(self.dc_context, name, value)

    def start(self):
        deltachat.set_context_callback(self.dc_context, self.process_event)
        capi.lib.dc_configure(self.dc_context)
        self._threads = IOThreads(self.dc_context)
        self._threads.start()

    def shutdown(self):
        deltachat.clear_context_callback(self.dc_context)
        self._threads.stop(wait=False)
        # XXX actually we'd like to wait but the smtp/imap
        # interrupt idle calls do not seem to release the
        # blocking call to smtp|imap idle. This means we
        # also can't now close the database because the
        # threads might still need it
        # capi.lib.dc_close(self.dc_context)

    def process_event(self, ctx, evt_name, data1, data2):
        assert ctx == self.dc_context
        if self._logcallback is not None:
            self._logcallback((evt_name, data1, data2))
        callname = evt_name[3:].lower()
        method = getattr(self, callname, None)
        if method is not None:
            return method(data1, data2) or 0
        # print ("dropping event: no handler for", evt_name)
        return 0

    def read_url(self, url):
        try:
            r = requests.get(url)
        except requests.ConnectionError:
            return ''
        else:
            return r.content

    def event_http_get(self, data1, data2):
        url = data1.decode("utf-8")
        content =  self.read_url(url)
        s = content.encode("utf-8")
        # we need to return a fresh pointer that the core owns
        return capi.lib.dupstring_helper(s)

    def event_is_offline(self, data1, data2):
        return 0  # always online


class IOThreads:
    def __init__(self, dc_context):
        self.dc_context = dc_context
        self._thread_quitflag = False
        self._name2thread = {}

    def start(self, imap=True, smtp=True):
        assert not self._name2thread
        if imap:
            self._start_one_thread("imap", self.imap_thread_run)
        if smtp:
            self._start_one_thread("smtp", self.smtp_thread_run)

    def _start_one_thread(self, name, func):
        self._name2thread[name] = t = threading.Thread(target=func, name=name)
        t.setDaemon(1)
        t.start()

    def stop(self, wait=False):
        self._thread_quitflag = True
        # XXX interrupting does not quite work yet, the threads keep idling
        print("interrupting smtp and idle")
        capi.lib.dc_interrupt_imap_idle(self.dc_context)
        capi.lib.dc_interrupt_smtp_idle(self.dc_context)
        if wait:
            for name, thread in self._name2thread.items():
                thread.join()

    def imap_thread_run(self):
        print ("starting imap thread")
        while not self._thread_quitflag:
            capi.lib.dc_perform_imap_jobs(self.dc_context)
            capi.lib.dc_perform_imap_fetch(self.dc_context)
            capi.lib.dc_perform_imap_idle(self.dc_context)

    def smtp_thread_run(self):
        print ("starting smtp thread")
        while not self._thread_quitflag:
            capi.lib.dc_perform_smtp_jobs(self.dc_context)
            capi.lib.dc_perform_smtp_idle(self.dc_context)