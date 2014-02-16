#!/usr/bin/python
"""Run an executable on a platform

Usage:
    platformrun [-v | -vv] [options] PLATFORM EXECUTABLE
    platformrun -h

Options:
    -h --help           Show this usage message
    -c --config CONF    Specify the measurement configuration to load
                            [default: measurement.json]
    -v --verbose        Be verbose

    PLATFORM        Specify the platform on which to run.
                    Available platforms are:
                        stm32f0discovery
                        atmega328p
                        msp-exp430f5529


"""
from docopt import docopt

import subprocess, os
import threading
from collections import namedtuple
import tempfile
import json

import pyenergy
from time import sleep

import logging

logger = logging.getLogger(__name__)
warning = logger.warning
debug = logger.debug

# Global config options

stlink = "/home/james/tools/stlink/st-util"
arm_gdb = "arm-none-eabi-gdb"

avrdude = "avrdude"
avr_objcopy = "avr-objcopy"

pic32_objcopy = "pic32-objcopy"
pic32prog = "~/tools/pic32prog/pic32prog"

mspdebug = "~/tools/mspdebug/mspdebug"

measurement_config = None

#######################################################################

def gdb_launch(gdbname, port, fname):
    if logger.getEffectiveLevel() == logging.DEBUG:
        silence = "-batch"
    else:
        silence = "-batch-silent"

    cmdline = '{gdbname} {silence} -ex "set confirm off" \
                  -ex "tar ext :{port}" \
                  -ex "monitor reset halt" \
                  -ex "load" \
                  -ex "delete breakpoints" \
                  -ex "break exit" \
                  -ex "break _exit" \
                  -ex "continue" \
                  -ex "quit" \
                  {fname}'.format(**locals())
    os.system(cmdline)

def background_proc(cmd, stdout=None, stderr=None):

    def run_proc(ev):
        debug("Starting background proc: \"{}\"".format(cmd))
        if logger.getEffectiveLevel() == logging.DEBUG:
            p = subprocess.Popen(cmd, shell=True)
        else:
            p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while not ev.isSet():
            if logger.getEffectiveLevel() == logging.DEBUG:
                out = p.stdout.read(1)
                err = p.stderr.read(1)
            else:
                out = err = ''
            if (out == '' or err == '') and p.poll() != None:
                break
            ev.wait(0.1)
        debug("Killing background proc: \"{}\"".format(cmd))
        p.kill()

    ev = threading.Event()
    t = threading.Thread(target=run_proc, args=(ev,))
    t.start()

    return ev

def kill_background_proc(p):
    p.set()

def foreground_proc(cmd):
    debug("Starting foreground proc: \"{}\"".format(cmd))
    if logger.getEffectiveLevel() == logging.DEBUG:
        p = subprocess.Popen(cmd, shell=True)
    else:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while True:
        if logger.getEffectiveLevel() == logging.DEBUG and p.stdout:
            out = p.stdout.read(1)
        else:
            out = ''

        if logger.getEffectiveLevel() == logging.DEBUG and p.stderr:
            err = p.stderr.read(1)
        else:
            err =''
        if (out == '' or err == '') and p.poll() != None:
            break
    

def setupMeasurement(platform):
    em = pyenergy.EnergyMonitor(measurement_config[platform]['energy-monitor'])
    mp = int(measurement_config[platform]['measurement-point'])

    em.connect()
    em.enableMeasurementPoint(mp)
    em.clearNumberOfRuns(mp)
    em.measurement_params[mp]['resistor'] = int(measurement_config[platform]['resistor'])
    em.setTrigger(measurement_config[platform]['trigger-pin'], mp)

    return em

def finishMeasurement(platform, em):
    mp = int(measurement_config[platform]['measurement-point'])

    while not em.measurementCompleted(mp):
        sleep(0.1)
    m = em.getMeasurement(mp)

    em.disconnect()
    return m

# Display units nicer
def prettyPrint(v):
    units = ['', 'm', 'u', 'n', 'p']

    for unit in units:
        if v > 1.0:
            return "{: >8.3f} {}".format(v, unit)
        v *= 1000.
    return "{}".format(v)

#######################################################################

def loadConfiguration(fname):
    global measurement_config

    measurement_config = json.load(open(fname))

#######################################################################

def stm32f0discovery(fname):
    em = setupMeasurement("stm32f0discovery")

    stproc = background_proc(stlink + " -p 2001 -c 0x0bb11477 -v0")
    gdb_launch(arm_gdb, 2001, fname)
    kill_background_proc(stproc)

    return finishMeasurement("stm32f0discovery", em)


def stm32vldiscovery(fname):
    em = setupMeasurement("stm32vldiscovery")

    stproc = background_proc(stlink + " -p 2002 -c 0x1ba01477 -v0")
    gdb_launch(arm_gdb, 2002, fname)
    kill_background_proc(stproc)

    return finishMeasurement("stm32vldiscovery", em)


def atmega328p(fname):
    em = setupMeasurement("atmega328p")

    # Create temporary file and convert to hex file
    tf = tempfile.NamedTemporaryFile(delete=False)
    tf.close()
    foreground_proc("{} -O ihex {} {}".format(avr_objcopy, fname, tf.name))

    # Flash the hex file to the AVR chip
    ser_id = measurement_config['atmega328p']['serial-dev-id']
    cmdline = "{} -F -V -c arduino -p atmega328p -e -P `readlink -m /dev/serial/by-id/{}` -b 115200 -U flash:w:{}".format(avrdude, ser_id, tf.name)
    foreground_proc(cmdline)

    os.unlink(tf.name)
    return finishMeasurement("atmega328p", em)


def mspexp430f5529(fname):
    em = setupMeasurement("msp-exp430f5529")

    foreground_proc("{} tilib -q \"prog {}\" &".format(mspdebug, fname))

    return finishMeasurement("msp-exp430f5529", em)


def mspexp430fr5739(fname):
    em = setupMeasurement("msp-exp430fr5739")

    foreground_proc("{} rf2500 -q \"prog {}\" &".format(mspdebug, fname))

    return finishMeasurement("msp-exp430fr5739", em)


def pic32mx250f128b(fname):
    # Create temporary file and convert to hex file
    tf = tempfile.NamedTemporaryFile(delete=False)
    tf.close()
    foreground_proc("{} -O ihex {} {}".format(pic32_objcopy, fname, tf.name))

    # Program the PIC and leave power on to run test
    em = setupMeasurement("pic32mx250f128b")
    foreground_proc("{} -p {}".format(pic32prog, tf.name))

    os.unlink(tf.name)
    return finishMeasurement("pic32mx250f128b", em)


if __name__ == "__main__":
    arguments = docopt(__doc__)

    logging.basicConfig()

    if arguments['--verbose'] == 1:
        logging.getLogger('').setLevel(logging.INFO)
    elif arguments['--verbose']== 2:
        logging.getLogger('').setLevel(logging.DEBUG)

    loadConfiguration(arguments['--config'])

    if arguments['PLATFORM'] == "stm32f0discovery":
        m = stm32f0discovery(arguments['EXECUTABLE'])
    if arguments['PLATFORM'] == "stm32vldiscovery":
        m = stm32vldiscovery(arguments['EXECUTABLE'])
    elif arguments['PLATFORM'] == "atmega328p":
        m = atmega328p(arguments['EXECUTABLE'])
    elif arguments['PLATFORM'] == "pic32mx250f128b":
        m = pic32mx250f128b(arguments['EXECUTABLE'])
    elif arguments['PLATFORM'] == "msp-exp430f5529":
        m = mspexp430f5529(arguments['EXECUTABLE'])
    elif arguments['PLATFORM'] == "msp-exp430fr5739":
        m = mspexp430fr5739(arguments['EXECUTABLE'])
    else:
        raise RuntimeError("Unknown platform " + arguments['PLATFROM'])
        
    print "Energy:          {}J".format(prettyPrint(m.energy))
    print "Time:            {}s".format(prettyPrint(m.time))
#    print "Power:           {}W".format(prettyPrint(m.avg_power))
    print "Average current: {}A".format(prettyPrint(m.avg_current))
    print "Average voltage: {}V".format(prettyPrint(m.avg_voltage))
