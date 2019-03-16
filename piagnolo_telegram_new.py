from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import telegram
import paho.mqtt.client as mqtt
import threading
import queue
import time
import logging
import paho.mqtt.publish as publish
import datetime
import matplotlib
matplotlib.use('Pdf')
import matplotlib.pyplot as plt
from logging.handlers import RotatingFileHandler

hostname = "localhost"

DEBUG = False
logFile = 'mqtt_telegram.log'
log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

log_handler = RotatingFileHandler(logFile, mode='a', maxBytes=5*1024*1024, backupCount=2, encoding=None, delay=0)
log_handler.setFormatter(log_formatter)
log_handler.setLevel(logging.DEBUG)

prog_log = logging.getLogger('root')
prog_log.setLevel(logging.DEBUG)
prog_log.addHandler(log_handler)

prog_log.info("Start")
queueLock = threading.Lock()
queue_to_telegram = queue.Queue(10)
requestLock = threading.Lock()
request_queue = queue.Queue(10)


def build_menu(buttons,
               n_cols,
               header_buttons=None,
               footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]
    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)
    return menu


class Request:
    def __init__(self, name, bot, chat_id, args):
        self.name = name
        self.args = args
        self.chat_id = chat_id
        self.bot = bot


class telegram_thread(threading.Thread):
    def __init__(self, queue_to_telegram):
        threading.Thread.__init__(self)
        self.queue_to_telegram = queue_to_telegram
        self.last_temperature_sala = None
        self.last_humidity_sala = None
        self.temp = []
        self.temp_time = []
        self.hum = []
        self.hum_time = []
        self.max_buffer_size = 10000
        self.bot = None
        self.default = 16
        self.actual_setpoint = self.default
        self.heater_enabled = False

    def run(self):
        while True:
            requestLock.acquire()
            if not request_queue.empty():
                #there is a new request from the user
                next_req = request_queue.get()
                requestLock.release()
            else:
                next_req = None
                requestLock.release()

            #handle the next req
            if next_req is not None:
                # if the bot is not defined, use this message to instantiate it
                if self.bot is None:
                    self.bot = next_req.bot
                if next_req.name == "/home/sala/temperature":
                    self.bot.send_message(chat_id=next_req.chat_id, text=r'Temperatura sala: {}, umidita sala: {}'.format(self.last_temperature_sala, self.last_humidity_sala))
                    prog_log.debug('Replying to temperature request to {}'.format(next_req.chat_id))
                if next_req.name == "home/sala/stufa":
                    if float(next_req.args[0]) > 15 and float(next_req.args[0]) < 24:
                        self.actual_setpoint = float(next_req.args[0])
                        self.heater_enabled = True
                        publish.single("home/sala/stufa", "1", hostname=hostname, port=1883)
                    else:
                        self.actual_setpoint = self.default
                        self.heater_enabled = False
                        publish.single("home/sala/stufa", "0", hostname=hostname, port=1883)
                if next_req.name == "home/sala/grafico":
                    fig,ax1 = plt.subplots()
                    ax1.plot(self.temp_time, self.temp, 'b-o')
                    ax1.set_xlabel('time (s)')
                    # Make the y-axis label, ticks and tick labels match the line color.
                    ax1.set_ylabel('Temperatura', color='b')
                    ax1.tick_params('y', colors='b')

                    ax2 = ax1.twinx()
                    ax2.plot(self.hum_time, self.hum, 'r-o')
                    ax2.set_ylabel('Umidita', color='r')
                    ax2.tick_params('y', colors='r')

                    fig.tight_layout()
                    plt.savefig('temp.png')
                    self.bot.send_photo(chat_id=next_req.chat_id, photo=open('temp.png','rb'))
                    prog_log.debug('Replying to plot request to {}'.format(next_req.chat_id))
            queueLock.acquire()
            if not queue_to_telegram.empty():
                # a new message is available on the queue
                msg = queue_to_telegram.get()
                queueLock.release()
                # output = "Received data {} from {}".format(msg.payload.decode('utf-8'), msg.topic)
                # print(output)
                try:
                    val = msg.payload.decode('utf-8')
                    numeric_val = float(val)
                    if msg.topic == 'home/sala/temperature':
                        #temp control
                        if self.heater_enabled:
                            if numeric_val < self.actual_setpoint:
                                # turn on
                                publish.single("home/sala/stufa", "1", hostname=hostname, port=1883)
                            else:
                                # turn off
                                publish.single("home/sala/stufa", "0", hostname=hostname, port=1883)
                        self.last_temperature_sala = numeric_val
                        self.temp.append(numeric_val)
                        self.temp_time.append(datetime.datetime.now())
                        if len(self.temp) >= self.max_buffer_size:
                            self.temp = self.temp[-self.max_buffer_size:]
                            self.temp_time = self.temp_time[-self.max_buffer_size:]
                    elif msg.topic == 'home/sala/humidity':
                        self.last_humidity_sala = numeric_val
                        self.hum.append(numeric_val)
                        self.hum_time.append(datetime.datetime.now())
                        if len(self.hum) >= self.max_buffer_size:
                            self.hum = self.hum[-self.max_buffer_size:]
                            self.hum_time = self.hum_time[-self.max_buffer_size:]
                except:
                    prog_log.critical('Unable to convert to int {}'.format(msg.payload.decode('utf-8')))
                #self.bot.send_message(chat_id=self.chat_id, text=output)
            else:
                queueLock.release()
            time.sleep(0.01)


class mqtt_thread(threading.Thread):
    def __init__(self, queue_to_telegram, queue_to_mqtt):
        threading.Thread.__init__(self)
        self.queue_to_telegram = queue_to_telegram
        self.queue_to_mqtt = queue_to_mqtt


