import pytest
from unittest import mock

from htpclient.hashcat_cracker import HashcatCracker
from htpclient.binarydownload import BinaryDownload

from argparse import Namespace

# The default cmdparameters, some objects need those. Maybe move to a common helper so other tests can include this aswell.
# test_args = Namespace( cert=None,  cpu_only=False, crackers_path=None, de_register=False, debug=True, disable_update=False, files_path=None, hashlists_path=None, number_only=False, preprocessors_path=None, url='http://example.com/api/server.php', version=False, voucher='devvoucher', zaps_path=None)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# PoC testing/development framework for APIv2
# Written in python to work on creation of hashtopolis APIv2 python binding.
#
import json
import requests
import unittest
import datetime
from pathlib import Path

import requests
import unittest
import logging
from pathlib import Path
import abc

import http
import confidence

#logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)

HTTP_DEBUG = False

# Monkey patching to allow http debugging
if HTTP_DEBUG:
    http_logger = logging.getLogger('http.client')
    http.client.HTTPConnection.debuglevel = 0
    def print_to_log(*args):
        http_logger.debug(" ".join(args))
    http.client.print = print_to_log

cls_registry = {}


class Config(object):
    def __init__(self):
        # Request access TOKEN, used throughout the test
        load_order = confidence.DEFAULT_LOAD_ORDER + (str(Path(__file__).parent.joinpath('{name}.{extension}')),)
        self._cfg = confidence.load_name('hashtopolis-test', load_order=load_order)
        self._hashtopolis_uri = self._cfg['hashtopolis_uri']
        self._api_endpoint = self._hashtopolis_uri + '/api/v2'
        self.username = self._cfg['username']
        self.password = self._cfg['password']

    


class HashtopolisConnector(object):
    # Cache authorisation token per endpoint
    token = {}
    token_expires = {}

    def __init__(self, model_uri, config):
        self._model_uri = model_uri
        self._api_endpoint = config._api_endpoint
        self._hashtopolis_uri = config._hashtopolis_uri
        self.config = config

    def authenticate(self):    
        if not self._api_endpoint in HashtopolisConnector.token:
            # Request access TOKEN, used throughout the test

            logger.info("Start authentication")
            auth_uri = self._api_endpoint + '/auth/token'
            auth = (self.config.username, self.config.password)
            r = requests.post(auth_uri, auth=auth)

            HashtopolisConnector.token[self._api_endpoint] = r.json()['token']
            HashtopolisConnector.token_expires[self._api_endpoint] = r.json()['token']

        self._token = HashtopolisConnector.token[self._api_endpoint]
        self._token_expires = HashtopolisConnector.token_expires[self._api_endpoint]

        self._headers = {
            'Authorization': 'Bearer ' + self._token,
            'Content-Type': 'application/json'
        }


    def get_all(self):
        self.authenticate()

        uri = self._api_endpoint + self._model_uri
        headers = self._headers
        payload = {}

        r = requests.get(uri, headers=headers, data=json.dumps(payload))
        return r.json()['values']

    def patch_one(self, obj):
        if not obj.has_changed():
            logger.debug("Object '%s' has not changed, no PATCH required", obj)
            return

        self.authenticate()
        uri = self._hashtopolis_uri + obj._self
        headers = self._headers
        payload = {}

        for k,v in obj.diff().items():
            logger.debug("Going to patch object '%s' property '%s' from '%s' to '%s'", obj, k, v[0], v[1])
            payload[k] = v[1]

        r = requests.patch(uri, headers=headers, data=json.dumps(payload))
        if r.status_code != 201:
            logger.exception("Patching failed: %s", r.text)

        # TODO: Validate if return objects matches digital twin
        obj.set_initial(r.json().copy())

    def create(self, obj):
        # Check if object to be created is new
        assert(not hasattr(obj, '_self'))

        self.authenticate()
        uri = self._api_endpoint + self._model_uri
        headers = self._headers
        payload = dict([(k,v[1]) for (k,v) in obj.diff().items()])

        r = requests.post(uri, headers=headers, data=json.dumps(payload))
        if r.status_code != 201:
            logger.exception("Creation of object failed: %s", r.text)

        # TODO: Validate if return objects matches digital twin
        obj.set_initial(r.json().copy())


    def delete(self, obj):
        """ Delete object from database """
        # TODO: Check if object to be deleted actually exists
        assert(hasattr(obj, '_self'))

        self.authenticate()
        uri = self._hashtopolis_uri + obj._self
        headers = self._headers
        payload = {}


        r = requests.delete(uri, headers=headers, data=json.dumps(payload))
        if r.status_code != 204:
            logger.exception("Deletion of object failed: %s", r.text)

        # TODO: Cleanup object to allow re-creation


class ManagerBase(type):
    conn = {}
    # Cache configuration values
    config = None
    @classmethod
    def get_conn(cls):
        if cls.config is None:
            cls.config = Config()

        if cls._model_uri not in cls.conn:
            cls.conn[cls._model_uri] = HashtopolisConnector(cls._model_uri, cls.config)
        return cls.conn[cls._model_uri]

    @classmethod
    def all(cls):
        """
        Retrieve all backend objects
        TODO: Make iterator supporting loading of objects via pages
        """
        # Get all objects
        api_objs = cls.get_conn().get_all()


        # Convert into class
        objs = []
        for api_obj in api_objs:
            new_obj = cls._model(**api_obj)
            objs.append(new_obj)
        return objs

    @classmethod
    def patch(cls, obj):
        cls.get_conn().patch_one(obj)

    @classmethod
    def create(cls, obj):
        cls.get_conn().create(obj)

    @classmethod
    def delete(cls, obj):
        cls.get_conn().delete(obj)

    @classmethod
    def get_first(cls):
        """
        Retrieve first object
        TODO: Error handling if first object does not exists
        TODO: Request object with limit parameter instead
        """
        return cls.all()[0]

# Build Django ORM style 'ModelName.objects' interface
class ModelBase(type):
    def __new__(cls, clsname, bases, attrs, uri=None, **kwargs):
        parents = [b for b in bases if isinstance(b, ModelBase)]
        if not parents:
            return super().__new__(cls, clsname, bases, attrs)

        new_class = super().__new__(cls, clsname, bases, attrs)
        
        setattr(new_class, 'objects', type('Manager', (ManagerBase,), {'_model_uri': uri}))
        setattr(new_class.objects, '_model', new_class)
        cls_registry[clsname] = new_class

        return new_class


class Model(metaclass=ModelBase):
    def __init__(self, *args, **kwargs):      
        self.set_initial(kwargs)
        super().__init__()

    def set_initial(self, kv):
        self.__fields = []
        # Store fields allowing us to detect changed values
        if '_self' in kv:
            self.__initial = kv.copy()
        else:
            # New model
            self.__initial = {}

        # Create attribute values
        for k,v in kv.items():
            setattr(self, k, v)
            if not k.startswith('_'):
                self.__fields.append(k)


    def diff(self):
        d1 = self.__initial
        d2 = dict([(k, getattr(self, k)) for k in self.__fields])
        diffs = [(k, (v, d2[k])) for k, v in d2.items() if v != d1.get(k, None)]
        return dict(diffs)

    def has_changed(self):
        return bool(self.diff())

    def save(self):
        if hasattr(self, '_self'):
            self.objects.patch(self)
        else:
            self.objects.create(self)

    def delete(self):
        if hasattr(self, '_self'):
            self.objects.delete(self)

    def serialize(self):
        return [x for x in vars(self) if not x.startswith('_')]


class Task(Model, uri="/ui/tasks"):
    def __repr__(self):
        return self._self


class Hashlist(Model, uri="/ui/hashlists"):
    def __repr__(self):
        return self._self


class HashcatCrackerTestLinux(unittest.TestCase):
    def test_correct_flow(self):
        # Create hashlist
        p = Path(__file__).parent.joinpath('create_hashlist_001.json')
        payload = json.loads(p.read_text('UTF-8'))
        hashlist = Hashlist(**payload)
        hashlist.save()

        # Create Task
        for p in sorted(Path(__file__).parent.glob('create_task_001.json')):
            payload = json.loads(p.read_text('UTF-8'))
            payload['hashlistId'] = int(hashlist._id)
            obj = Task(**payload)
            obj.save()

        # 
        test_args = Namespace( cert=None,  cpu_only=False, crackers_path=None, de_register=False, debug=True, disable_update=False, files_path=None, hashlists_path=None, number_only=False, preprocessors_path=None, url='http://hashtopolis/api/server.php', version=False, voucher='devvoucher', zaps_path=None)
        binaryDownload = BinaryDownload(test_args)
        binaryDownload.check_version(1)
        hashcat = HashcatCracker(1, binaryDownload)

        # Cleanup
        obj.delete()
        hashlist.delete()

if __name__ == '__main__':
    unittest.main()


# @mock.patch('htpclient.initialize.Initialize.get_os')
# @mock.patch('subprocess.check_output')
# @mock.patch('htpclient.jsonRequest.JsonRequest.execute')
# @mock.patch('htpclient.download.Download.download')
# @mock.patch('os.system')
# @mock.patch('os.unlink')
# def test_hashcat_cracker_linux(mock_unlink, mock_system, mock_download, mock_get, mock_check_output, mock_get_os):
#     #TODO: Make paths based on environment
#     #TODO: Clean all cracker folders etc

#     # Force Linux OS
#     mock_get_os.return_value = 0

#     binaryDownload = BinaryDownload(test_args)

#     # When calling binaryDownload.check_version(1), this will make a request for executable name
#     # Download the 7z if the cracker is not there
#     # Extract the 7z
#     # And cleanup the temp file
    
#     mock_get.return_value = {'response': 'SUCCESS', 'url': 'leeg', 'executable': 'hashcat.bin'}
#     mock_download.return_value = True
#     mock_check_output.return_value = 'v6.2.6\n'.encode()
#     binaryDownload.check_version(1)

#     # Checking if system and unlink were called correctly.
#     mock_system.assert_called_with("./7zr x -o'/app/src/crackers/temp' '/app/src/crackers/1.7z'")
#     mock_unlink.assert_called_with("/app/src/crackers/1.7z")
    
#     # This will call 'hashcat --version'
#     hashcat = HashcatCracker(1, binaryDownload)
#     mock_check_output.assert_called_with("'./hashcat64.bin' --version", shell=True, cwd='/app/src/crackers/1/')
