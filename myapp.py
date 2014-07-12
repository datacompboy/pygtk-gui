import gui
import gtk
import threading
import time

# Sample APP, displaying usage of gui.py
# More info there: http://habrahabr.ru/post/120668/

class CMyApp(object):
    def __init__(self):
        self.label = None
        self.times = 0

    @gui.GtkLocked
    def CreateGui(self):
        window = gtk.Window(gtk.WINDOW_TOPLEVEL)
        window.set_title(u"Our cool app")
        window.connect("destroy", gui.GUIstop)
        window.realize()

        vbox = gtk.VBox()
        window.add(vbox)

        label = gtk.Label("Welcome to our coool app!")
        vbox.pack_start(label)

        label = gtk.Label("Here will be counter")
        self.label = label
        vbox.pack_start(label)

        button = gtk.Button("Press me!")
        button.connect("clicked", self.Click)
        vbox.pack_start(button)

        button = gtk.Button("Or me!")
        button.connect("clicked", gui.GtkLocked(self.Count))
        vbox.pack_start(button)

        progress = gtk.ProgressBar()
        self.progress = progress 
        vbox.pack_start(progress)
        T = threading.Thread(name="Background work", target=self.Generate)
        T.setDaemon(1)
        T.start()

        fastprogress = gtk.ProgressBar()
        self.fastprogress = fastprogress
        vbox.pack_start(fastprogress)
        T = threading.Thread(name="Heavy background work", target=self.GenerateFast)
        T.setDaemon(1)
        T.start()

        window.show_all()

    @gui.GtkLocked
    def Click(self, widget):
        self.times += 1
        self.label.set_text("You pressed button %d times" % self.times)

    @gui.GtkLocked
    def Click(self, widget):
        self.Count()
        gui.GuiPeriodCall( self.Count )

    def Count(self, *args, **kwargs):
        with gui.GtkLocker:
            self.times += 1
            self.label.set_text("You pressed button %d times" % self.times)

    @gui.GtkLocked
    def UpdateProgress(self):
        self.progress.pulse()

    def Generate(self):
        while(True):
            time.sleep(0.3)
            gui.GuiIdleCall( self.UpdateProgress )

    @gui.PeriodUpdater
    def SingleUpdateProgress(self):
        self.fastprogress.pulse()

    def GenerateFast(self):
        while(True):
            time.sleep(0.001)
            self.SingleUpdateProgress()

MyApp = CMyApp()
def InitApp():
    """ Create & display required controls """
    MyApp.CreateGui()

def __main__():
    gui.GuiCall( InitApp )
    gui.GUI()

if __name__ == '__main__':
    __main__()

