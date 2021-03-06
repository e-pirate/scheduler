#!/usr/bin/env python3.9
# vim: lw=-c\ scheduler.yaml

import sys
import argparse
import logging
import logging.handlers
import yaml
from yaml.composer import Composer
from yaml.constructor import SafeConstructor
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import BaseResolver
from yaml.scanner import Scanner
import re
import time
import os
import json
from datetime import datetime, timedelta
import asyncio

_version_ = '0.0.1'
_author_ = 'Artem Illarionov <e-pirate@mail.ru>'

def checkcond_time(condition: dict) -> bool:
#TODO: check if start is preore stop, duration < 1d + call check_cnd_time function and check for exceptions
    now = datetime.now()

    def srtstp2tddt(timestr):
        if timestr.count(':') == 1:
            return(datetime.combine(now.date(), datetime.strptime(timestr, "%H:%M").time()))
        elif timestr.count(':') == 2:
            return(datetime.combine(now.date(), datetime.strptime(timestr, "%H:%M:%S").time()))
        raise ValueError

    if 'stop' in condition and now > srtstp2tddt(condition['stop']):                                # stop time is set and we already passed it
        return(False)

    start = srtstp2tddt(condition['start'])

    if 'duration' in condition:
        duration = condition['duration'].lower()
        hours = minutes = seconds = 0
        if 'h' in duration:
            hours, duration = duration.split('h')
        if 'm' in duration:
            minutes, duration = duration.split('m')
        if 's' in duration:
            seconds, duration = duration.split('s')
        duration = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))

        if (start + duration).day <= now.day:                                                       # check if task ends today
            if now > start + duration:                                                              # check if we already passed end time
                return(False)
        else:                                                                                       # task will end tomorrow
            if start + duration - timedelta(days=1) < now < start:                                  # check if we already passed the remainig part of the end time
                return(False)                                                                       # or did't reached start time yet
            else:                                                                                   # we are still withing the remainig part of the end time
                return(True)                                                                        # return True now, as we are still withing the remaining part

    if now < start:                                                                                 # did not reached start time yet
        return(False)

    return(True)

def checkcond_state(condition: str) -> bool:
    return(True)

def checkcond_power(condition: str) -> bool:
    return(True)

def checkcond(condition: str) -> bool:
    if condition['type'] == 'time':
        return(checkcond_time(condition))
    if condition['type'] == 'state':
        return(checkcond_state(condition))
    if condition['type'] == 'power':
        return(checkcond_power(condition))

#TODO: возвращять из каждой функции, проверяющей таск true, если статус изменился, проверять если в очереди незавершенные задачи на прверку тасков. Если текущий
# последний и хотябы один вернул истину, запустить еще один диспатчер проверки всех статусов, но без встроенного продолжателя
# unknown -> inactive -> scheduled -> pending -> active
async def task_loop(tasks: dict, statedb: dict):
    log = logging.getLogger("__main__") 
    log.info('Entering task event loop..')
    while True:
#        log.debug('Task cycle')
        nextrun_uts = int(time.time()) + 1                                                              # Save round second for the next cycle to be run
        state_update = False 
        for task in tasks:
            for state in tasks[task]['states']:
                if state['name'] == 'default':                                                          # Skip default state
                    continue
                status = 'active'
                for condition in state['conditions']:                                                   # Cycle through all conditions for the current state
                    if not checkcond(condition):                                                        # Check if current condition failed
                        status = 'inactive'
                        break                                                                           # Stop checking conditions on first failure
                if statedb[task][state['name']] != status:
                    if status == 'active':
                        if statedb[task][state['name']] in ['scheduled', 'pending']:
                            break
                        else:
                            status = 'scheduled'
                    log.debug('Chaging ' + task + ' state ' + state['name'] + ' ' + statedb[task][state['name']] + ' -> ' + status)
                    statedb[task][state['name']] = status
                    state_update = True

            # Check if default state is present and should be activated for current task
            if 'default' in statedb[task]:
                default = True
                for name in statedb[task]:
                    if name != 'default' and statedb[task][name] in ['scheduled', 'pending', 'active']:
                        default = False
                        break
                if default:
                    if statedb[task]['default'] not in ['scheduled', 'pending' 'active']:
                        log.debug('Chaging ' + task + ' state default ' + statedb[task]['default'] + ' -> scheduled')
                        statedb[task]['default'] = 'scheduled'
                else: 
                    if statedb[task]['default'] in ['scheduled', 'pending' 'active']:
                        log.debug('Chaging ' + task + ' state default ' + statedb[task]['default'] + ' -> inactive')
                        statedb[task]['default'] = 'inactive'
#        print(json.dumps(statedb, indent=2, sort_keys=True))

        if state_update:
            log.debug('State update is scheduled')

        if not state_update:
            await asyncio.sleep(nextrun_uts - time.time())                                              # Wait if no state updates scheduled or till upcoming second

async def state_loop():
    log = logging.getLogger("__main__") 
    log.info('Entering state event loop..')
    while True:
#        log.debug('State cycle')
        await asyncio.sleep(0.5)

async def main_loop(tasks, statedb):
    await asyncio.gather(task_loop(tasks, statedb), state_loop())

def main():
    parser = argparse.ArgumentParser(add_help=True, description='Aquarium scheduler and queue manager daemon.')
    parser.add_argument('-c', nargs='?', required=True, metavar='file', help='Scheduler configuration file in YAML format', dest='config')
#TODO: Реализовать опцию проверки конфигурации
    parser.add_argument('-t', nargs='?', metavar='test', help='Test devices and tasks according to specified configuration', dest='test')
    args = parser.parse_args()

    """ Load configuration from YAML """
    try:
        with open(args.config) as f:
            config = yaml.safe_load(f)
    except OSError as e:
        sys.exit('scheduler: (C) Failed to load config: ' + str(e.strerror) + ': \'' + str(e.filename) + '\'')
    except yaml.YAMLError as e:
        sys.exit('scheduler: (C) Failed to parse config: ' + str(e))


    """ Setup logging """
    def setLogDestination(dst):
        if dst == 'console':
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(fmt='%(asctime)s.%(msecs)03d scheduler: (%(levelname).1s) %(message)s', datefmt="%H:%M:%S"))
        elif dst == 'syslog':
            handler = logging.handlers.SysLogHandler(facility=logging.handlers.SysLogHandler.LOG_DAEMON, address = '/dev/log')
            handler.setFormatter(logging.Formatter(fmt='scheduler[%(process)d]: (%(levelname).1s) %(message)s'))
        else:
            raise ValueError
        log.handlers.clear()
        log.addHandler(handler)

    # Configure default logger
    log = logging.getLogger(__name__)
    setLogDestination('syslog')
    log.setLevel(logging.INFO)

    try:
        setLogDestination(config['log']['destination'].lower())
    except KeyError:
        log.error('Failed to configure log: Destination is undefined. Failing over to syslog.')
    except ValueError:
        log.error('Failed to configure log: Unknown destination: \'' + config['log']['destination'] + '\'. Failing over to syslog.')
     
    try:
        log.setLevel(config['log']['level'].upper())
    except KeyError:
        log.error('Failed to configure log: Log level is undefined. Failing over to info.')
    except ValueError:
        log.error('Failed to configure log: Unknown level: \'' + config['log']['level'] + '\'. Failing over to info.')

    log.info('Starting scheduler v' + _version_ + '..')
    log.debug('Log level set to: ' + logging.getLevelName(log.level))

    class CustomResolver(BaseResolver):
        pass

    CustomResolver.add_implicit_resolver(
       u'tag:yaml.org,2002:bool',
       re.compile(u'''^(?:true|True|TRUE|false|False|FALSE)$''', re.X),
       list(u'tTfF'))

    class CustomLoader(Reader, Scanner, Parser, Composer, SafeConstructor, CustomResolver):
        def __init__(self, stream):
            Reader.__init__(self, stream)
            Scanner.__init__(self)
            Parser.__init__(self)
            Composer.__init__(self)
            SafeConstructor.__init__(self)
            CustomResolver.__init__(self)

    devices = {}
    for entry in os.scandir(config['devices']):
        if entry.is_file() and (entry.name.endswith(".yaml") or entry.name.endswith(".yml")):
            with open(entry.path) as f:
                newdyaml = yaml.load(f, Loader=CustomLoader)
            for newdev in newdyaml:
                if newdev not in devices: # TODO: should be moved to pre check procedure
                    devices = {**devices, newdev: newdyaml[newdev]}
                else:
                    log.error('Peripheral device: \'' + newdev + '\' already exist')

    if len(devices) == 0:
        log.crit('No peripheral devices found, unable to continue')
        sys.exit(1)
    log.info('Found ' + str(len(devices)) + ' peripheral device(s)')

    tasks = {}
    for entry in os.scandir(config['tasks']):
        if entry.is_file() and (entry.name.endswith(".yaml") or entry.name.endswith(".yml")):
            with open(entry.path) as f:
                newtyaml = yaml.load(f, Loader=CustomLoader)
            for newtask in newtyaml:
                if newtask not in tasks: # TODO: should be moved to pre check procedure
                    tasks = {**tasks, newtask: newtyaml[newtask]}
                else:
                    log.error('Task: \'' + newtask + '\' already exist')

    if len(tasks) == 0:
        log.crit('No tasks found, unable to continue')
        sys.exit(1)
    log.info('Found ' + str(len(tasks)) + ' task(s)')

    # Create an empty state DB from all task states
    statedb = {}
    for task in tasks:
        statedb[task] = {}
        for state in tasks[task]['states']:
            statedb[task][state['name']] = 'unknown'

    if len(statedb) == 0:
        log.crit('Failed to form state DB, unable to continue')
        sys.exit(1)
    log.info('Formed state DB for ' + str(len(statedb)) + ' tasks')

#    print(json.dumps(statedb, indent=2, sort_keys=True))


    asyncio.run(main_loop(tasks, statedb))

#    log.critical('Failed')

    logging.shutdown()

if __name__ == "__main__":
    main()
