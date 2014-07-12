"""
    Common GUI code.

    Sould be run from main thread, will exit after main window closed.
"""
from __future__ import with_statement
import logging, traceback
logging.basicConfig(level=logging.DEBUG, filename='debug.log', filemode='a',
                    format='%(asctime)s %(levelname)-8s %(module)s %(funcName)s %(lineno)d %(threadName)s %(message)r')
import pygtk
pygtk.require('2.0')
import gtk
settings = gtk.settings_get_default()
gtk.gdk.threads_init()
gtk.gdk.threads_enter()
import gobject, gtk.glade, Queue, sys, configobj, threading, thread
from functools import wraps
import time, os, os.path
IGuiCaller = Queue.Queue()
IGuiIdleCaller = Queue.Queue()
GuiPeriod = 500 # 0.5 sec
IGuiPeriodCaller = Queue.Queue()
IdleCaller = [ None ]
IdleCallerLock = threading.Lock()

# =================================
# Reenterable locker
# 
# On windows it's always safe to reuse same lock, while on linux try to acquire already locked
# by same thread lock leads to deadlock.
# Also, it does checking for correct threading sticky to single thread, logging accidents.
#
# Could be used as
#   with gui.GtkLocker:
#     some-gtk-call()
#
# But since it still could lead to accidents, you should avoid that usage, and schedule ALL
# gtk actions via separate fun/lambda runned in gui context via IdleUpdater (see below)
#
class CGtkLocker:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = thread.get_ident() # Originally, main thread own lock
        self.mainthread = self.thread # Should log when locker used from different thread
        self.locked = 1
        self.calllock = True
        self.warn = True

    def __enter__(self, callLock = True, orig=""):
        with self.lock: DoLock = (thread.get_ident()!=self.thread)
        if self.warn and self.mainthread != thread.get_ident():
            logging.error("GUI accessed from wrong thread! %s\nTraceback: %s", orig, "".join(traceback.format_stack()))
        if DoLock:
            if callLock:
                #gtk.gdk.threads_enter()
                self.calllock = True
            else:
                self.calllock = False
            with self.lock: self.thread = thread.get_ident()
        self.locked += 1
        return None

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        with self.lock:
            self.locked -= 1
            if self.thread!=thread.get_ident():
                #print "!ERROR! Thread free not locked lock!"
                logging.error("Thread free not locked lock!")
                sys.exit(0)
            else:
                if self.locked == 0:
                    if self.calllock:
                        self.thread = None
                    self.calllock = True
                    #gtk.gdk.threads_leave()
        return None

    def FREE(self):
        self.locked -= 1
        self.thread = None
        if self.locked != 0:
            #print "!ERROR! Main free not before MAIN!"
            logging.error("Main free not before MAIN!")
            sys.exit(0)
    
    def Locked(self):
        return self.thread == thread.get_ident()
GtkLocker = CGtkLocker()

# ===========================================
# Pre-locked wrapper
#
# Use if with @gui.GtkLocked mark for functions, that called from GTK context
# (prevents deadlock with GtkLocker in function, that could be called as from GTK context,
#  as from plain code)
# You could as mark handler function itself, or use any function compatible with signal,
# by attaching wrapped function as signal:
#   button.connect("clicked", gui.GtkLocked(self.Count))
#
def GtkLocked(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        GtkLocker.__enter__(False, f.__module__ + "/" + f.__name__ + "()")
        ret = None
        try:
            ret = f(*args, **kwds)
        finally:
            GtkLocker.__exit__()
        return ret
    return wrapper

# ===========================================
# Wrapper, enforcing run of code in GUI context
#
# It's required to avoid deadlocks and accidental crashes do ALL changes in GUI from main thread.
# Easies way to achieve that, is just run updater lambda:
# def DoSomething():
#    voice = "mew"
#    @gui.GuiCalled
#    def Update():
#      somelabel.set_text("Cat said '%s'" % voice)
#    Update()
#
def GuiCalled(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        @wraps(f)
        def runner():
            return f(*args, **kwds)
        GuiIdleCall( runner )
    return wrapper

# ===========================================
# Wrapper, enforcing single call of code at next time GUI be free for that
#
# Since most of time you really doesn't need to update data ASAP, you could update all gui changes
# at single function. But you could change several places, so you need to call it from several
# place. Since it will display at once all changes, IdleUpdate wrapper is for you:
# @gui.IdleUpdater
# def updater():
#      somelabel.set_text("Cat said '%s'" % voice1)
#      somelabel.set_text("Dog said '%s'" % voice2)
# def mew():
#   voice1 += " mew"
#   updater()
# def woof():
#   voice2 += " woof"
# def both():
#   mew()
#   woof()
# once you call "both()", you should update both labels, but updater() will be called only once.
#
def IdleUpdater(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        self = len(args)>0 and isinstance(args[0], object) and args[0] or f
        if '_idle_wrapper' not in self.__dict__: self._idle_wrapper = {}
        @wraps(f)
        def runner():
            if self._idle_wrapper[f]:
                try: return f(*args, **kwds)
                finally: self._idle_wrapper[f] = False
            return None
        if f not in self._idle_wrapper or not self._idle_wrapper[f]:
            self._idle_wrapper[f] = True
            GuiIdleCall( runner )
    return wrapper

# ===========================================
# Wrapper, limiting rate of execution to at most one for period (0.5s currently)
#
# If you have any heavy or unknown-rate generating events process, there no need to update its
# status ASAP. It's enough to update one-two times per second; so you could easily
# call any update function, marked with @gui.PeriodUpdater, and it would be called at most 2 times
# per second (currently).
#
def PeriodUpdater(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        self = len(args)>0 and isinstance(args[0], object) and args[0] or f
        if '_idle_wrapper' not in self.__dict__: self._idle_wrapper = {}
        @wraps(f)
        def runner():
            if self._idle_wrapper[f]:
                try: return f(*args, **kwds)
                finally: self._idle_wrapper[f] = False
            return None
        if f not in self._idle_wrapper or not self._idle_wrapper[f]:
            self._idle_wrapper[f] = True
            GuiPeriodCall( runner )
        else:
            self._idle_wrapper[f] = f
    return wrapper

# ===========================================
# Put function to execution queue
#
# You could put one-line lambda to queue for update easily with it:
#   gui.GuiCall(lambda: somelabel.set_text("Wooof!"))
#
def GuiCall(Func):
    IGuiCaller.put(Func)
    with IdleCallerLock:
        if IdleCaller[0] == False:
            gobject.idle_add(GUIrun)
            IdleCaller[0] = True

# ===========================================
# Put function to idle execution queue
#
# There not much real difference with GuiCall, but you should use GuiIdleCall when you expect
# execution "once you have time", since there no guarantee to execute that function asap.
# Under heavy load it could run not all functions from idle queue
#
def GuiIdleCall(Func):
    IGuiIdleCaller.put(Func)
    with IdleCallerLock:
        if IdleCaller[0] == False:
            gobject.idle_add(GUIrun)
            IdleCaller[0] = True

# ===========================================
# Add function, that would be called periodically (every Period seconds)
#
def GuiPeriodCalls(Func, Period):
    gobject.timeout_add(int(Period*1000.0+0.5), Func)

# ===========================================
# Add function, that would be called periodically (every system-configured chunk)
#
# Differs from previous, since it would log if periodical function took too much time
#
def GuiPeriodCall(Func):
    IGuiPeriodCaller.put(Func)

@GtkLocked
def GUIrun(clean=True):
    try:
        t = time.time()
        n = 0
        while True:
            Run = IGuiCaller.get(0)
            try:
                n+=1
                t0 = time.time()
                Run()
                t0 = time.time()-t0
                if t0>0.1:
                    logging.info("Run=%r took %r", Run, t0)
            except Exception,E:
                logging.error("GUIrun/caller: %s\nRun = %r\nTraceback: %s", E, Run, traceback.format_exc())
            tt = time.time()
            if tt-t > 0.05:
                logging.error("GUIrun/caller: proceed %d for %r; left=%d", n, tt-t, IGuiCaller.qsize())
                break
    except Queue.Empty: pass
    try:
        t = time.time()
        n = 0
        while True:
            Run = IGuiIdleCaller.get(0)
            try:
                n+=1
                t0 = time.time()
                Run()
                t0 = time.time()-t0
                if t0>0.1:
                    logging.info("Run=%r took %r", Run, t0)
            except Exception,E:
                logging.error("GUIrun/idle: %s\nRun = %r\nTraceback: %s", E, Run, traceback.format_exc())
            tt = time.time()
            if tt-t > 0.05:
                logging.error("GUIrun/idle: proceed %d for %r; left=%d", n, tt-t, IGuiIdleCaller.qsize())
                break
    except Queue.Empty: pass
    if clean:
        with IdleCallerLock:
            IdleCaller[0] = IGuiCaller.qsize()>0 or IGuiIdleCaller.qsize()>0
            return IdleCaller[0]

oq = [1000, 1000, 1000]
@GtkLocked
def GUIrunPeriod():
    try:
        q = [ IGuiPeriodCaller.qsize(), IGuiCaller.qsize(), IGuiIdleCaller.qsize() ]
        if q[0] > 100 or q[1] > 100 or q[2] > 100:
            logging.info("IGuiPeriodCaller=%d(d %d) IGuiCaller=%d(d %d) IGuiIdleCaller=%d(d %d)" % ( q[0], q[0]-oq[0], q[1], q[1]-oq[1], q[2], q[2]-oq[2]))
        oq[:]=q[:] # Replace content in-place
        n=0
        t = time.time()
        try:
            while True:
                Run = IGuiPeriodCaller.get(0)
                try:
                    n+=1
                    t0 = time.time()
                    Run()
                    t0 = time.time()-t0
                    if t0>0.1:
                        logging.info("Run=%r took %r", Run, t0)
                except Exception,E:
                    logging.error("GUIrun/period: %s\nRun = %r\nTraceback: %s", E, Run, traceback.format_exc())
        except Queue.Empty: pass
        tt = time.time()
        if tt-t > 0.05:
            logging.error("GUIrunPrriod: proceed %d for %r", n, tt-t)
        GUIrun(False)
    except Queue.Empty:
        pass
    return True

# ===========================================
# Main gui processing loop
#
# You should append to Gui run queue function, that will create initial interface, and then
# enter GUI() loop forever. That MUST be main process thread, if you want to be sure you get no
# deadlock / crash somewhere deep inside GTK
#
def GUI():
    gobject.idle_add(GUIrun)
    IdleCaller[0] = True
    gobject.timeout_add(GuiPeriod, GUIrunPeriod)
    #gtk.gdk.threads_enter()
    GtkLocker.FREE() # Free it before enter into gtk.main
    gtk.main()
    #gtk.gdk.threads_leave()

# ===========================================
# Stop gui processing
#
# Call it from any thread/function to stop GUI: it whould be destroyed and GUI() function of main
# thread would exit.
#
@IdleUpdater
def GUIstop(*args):
    gtk.main_quit()

