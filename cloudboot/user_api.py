# This API allows user to manage services in multiple clouds.  It can
# launch/terminate/and report status on an entire boot plan.
#
# a boot plan is a series of levels that are run in serial.  each subsequent
# level depends on attributes of the previous and can thus not be started
# until the previous completes.
#
# each level contains a set of services to run.  All serices in a level
# can be started at the same time and have no dependency on each other.
# as a loose description, a service is a process running on a remote
# machine.  Often times part of starting the service includes launching
# a IaaS VM and configuring it, but this is not strictly needed.  Services
# can be started on existing machines as well.
#
# when a user creates a boot plan they describe each service with three
# major parts:
#  1) a VM image to launch OR an IP address where the service will be run
#  2) a contextualization document.  This is enough information to configure
#     the machine and run the needed services.
#  3) a ready program.  This is a script that verifies the service is up
#     and ready to go.
#
#  The configuration is described in detail elsewhere.  Here we show
#  the API for launching/terminating and gathering status for a bootplan

import os
import uuid
import time
import logging
from cloudboot.exceptions import APIUsageException, PollableException, ServiceException
from cloudboot.persistantance import CloudBootDB
from cloudboot.services import BootTopLevel, SVCContainer
import cloudboot

__author__ = 'bresnaha'

def config_get_or_none(parser, s, v):
    try:
        x = parser.get(s, v)
        return x
    except:
        return None


class CloudBoot(object):
    """
        This class is the top level boot description. It holds the parent Multilevel boot object which contains a set
        of many pollables.  The object also contains a way to get variable information from every service created.
        A service cannot be created without this object.  This object holds a dictionary of all services which is
        used for querying dependencies
    """
    
    def __init__(self, db_dir, config_file=None, db_name=None, log=logging, level_callback=None, service_callback=None):
        """
        db_dir:     a path to a directories where databases can be stored.

        config_file: the top_level config file describing this boot plan.
                    if this parameter is given then it is assumed that this
                    is a new launch plan.  if it is not given the db_name
                    parameter is required and the plan is loaded from an
                    existing database

        db_name:    the name of the database.  this is not an actual path
                    to a file, it is the run name given when the plan is
                    launched.  The run name can be found in self.name

        level_callback: a callback function that is invoked whenever
                        a level completes or a new level is started.  The signature of the callback is:

                        def func_name(cloudboot, action, current_level)

                        action is a string from the set
                        ["starting", "transition", "complete", "error"]

        service callback: a callbackfunciton that is invoked whenever
                        a service is started, progresses, or finishes.  The signature is:

                        def func_name(cloudservice, action, msg)

                        action is a string from the set:

                        ["starting", "transition", "complete", "error"]


        When this object is configured with a config_file a new sqlite
        database is created under @db_dir and a new name is picked for it.
        the data base ends up being called <db_dir>/cloudboot-<name>.db,
        but the user has no real need to know this.

        The contructor does not actually launch a run.  It simply loads up
        the database with the information in the config file (in the case
        of a new launch) and then builds the inmemory data structures.
        """

        if db_name == None and config_file == None:
            raise APIUsageException("Cloud boot must have a db_name or a config file to load")
        if not os.path.exists(db_dir):
            raise APIUsageException("Path to the give db does not exist: %s" % (db_name))

        if db_name == None:
            db_name = str(uuid.uuid4()).split("-")[0]

        db_path = "/%s/cloudboot-%s.db" % (db_dir, db_name)
        if config_file == None:
            if not os.path.exists(db_path):
                raise APIUsageException("Path to the db does not exist %s.  New dbs must be given a config file" % (db_path))

        self._log = log
        self._started = False
        self.run_name = db_name
        dburl = "sqlite://%s" % (db_path)

        self._db = CloudBootDB(dburl)

        if config_file != None:
            self._bo = self._db.load_from_conf(config_file)
        else:
            self._bo = self._db.load_from_db()

        self._levels = []
        self._boot_top = BootTopLevel(log=log, service_callback=self._mp_cb)
        for level in self._bo.levels:
            level_list = []
            for s in level.services:
                svc = self._boot_top.new_service(s, self._db)
                level_list.append(svc)

            self._boot_top.add_level(level_list)
            self._levels.append(level_list)

        self._level_callback = level_callback
        self._service_callback = service_callback

    def _mp_cb(self, mp, action, level_ndx):
        if self._level_callback:
            self._level_callback(self, action, level_ndx)

    # return a booting service for inspection by the user
    def get_service(self, svc_name):
        svc_dict = self._boot_top.get_services()
        return CloudService(svc_dict[svc_name])

    # get a list of all the services in the given level
    def get_level(self, level_ndx):
        svc_list = self._levels[level_ndx]
        cs_list = [CloudService(svc) for svc in svc_list]
        return cs_list

    def get_level_count(self):
        return len(self._levels)

    # poll the entire boot config until complete
    def block_until_complete(self, poll_period=0.1):
        """
        poll_period:        the time to wait inbetween calls to poll()

        This method is just a convenience loop around calls to poll.
        """
        if not self._started:
            raise APIUsageException("Boot plan must be started first.")

        done = False
        while not done:
            time.sleep(poll_period)
            done = self.poll()

    # poll one pass at the boot plan.
    def poll(self):
        """
        poll the launch plan.  This will through an exception if the
        start() has not yet been called.  An exception will also be
        thrown if any service experiences an error.  When this occurs
        the user can use the status() method to find exactly what went
        wrong.

        This will return False until the boot/ready test has completed
        either successfully or with an error.
        """
        if not self._started:
            raise APIUsageException("Boot plan must be started first.")
        rc = self._boot_top.poll()
        if rc:
            self._bo.status = 1
            self._db.db_commit()

    def start(self):
        """
        Begin launch plan.  If this is a new launch VMs will be started
        and boot configuration will occur before running the ready programs.
        If the services were already booted, just the ready program is run
        to test that everything is still working.

        This is an asynchronous call.  it just starts the process, poll()
        or block until complete must be called to check the status.

        After exeriencing an error a call to start can be made again.
        This will not restart any services.  That is up to the user
        to do by getting the failed services from error_status() and
        restarting them.  A call to start will always walk the list
        of levels in order.  It will start VM instances that have not
        yet been started, contextializes VMs tha thave not yet been
        contextualized, and call the ready program for all services.
        """

        self._boot_top.start()
        self._started = True

    def shutdown(self, dash_nine=False):
        pass

    def get_services(self):
        """
        Return an ordered lists of levels.  Each level is a list of
        of CloudService objects.  Users can interspect state with this
        """
        pass

    def error_status(self):
        """
        Like get services, only return just the services that had errors.
        If a level had no errors an empty list will be returned in that
        slot.
        """
        pass
    


class CloudService(object):

    def __init__(self, svc):
        """This should only be called by the CloudBoot object"""
        self._svc = svc
        self.name = svc.name

    def get_attr_from_bag(self, name):
        self._svc.get_dep(name)
    # need various methods for monitoring state. values from attr bag and from db

    def shutdown(self, dash_nine=False):
        """
        This will call the remote shutdown program associate with the
        service.  It is called asynchronously.  Poll just be called
        to make sure it have completed.

        if dash_nine is True the shutdown function will be skipped and
        the IaaS instance will be terminate (if the service has an
        IaaS instance.
        """

    def start(self):
        """
        This will restart the service, or check the results of the ready
        program if the serviceis already running.
        """
        pass

    def poll(self, service_callback=None):
        """
        service_callback:   let the user monitor progress of the shutdown
        or the restart.

        This function returns True when complete, or False if more polling
        is needed.  Exceptions are thrown if an error with the service
        occurs
        """
        pass


class CloudServiceException(ServiceException):
    def __init__(self, ex, svc):
        ServiceException.__init__(self, ex, svc)
        self.service = CloudService(svc)