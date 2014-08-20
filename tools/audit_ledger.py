#!/usr/bin/python
__author__ = 'sameer'

import os
import sys
import getpass

import string
import shlex
import textwrap
import autobahn.wamp1.protocol
import Crypto.Random.random

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
    "../server"))

from sputnik import config
from sputnik import database, models, util
from datetime import datetime
from sqlalchemy.orm.exc import NoResultFound
import time

session = database.make_session()
positions = session.query(models.Position).all()

adjust = True
print "BE SURE EVERYTHING IS SHUT BEFORE RUNNING THIS PROGRAM"
time.sleep(30)

def get_adjustment_position(contract):
    try:
        position = session.query(models.Position).filter_by(
                user=adjustment_user, contract=contract).one()
        return position
    except NoResultFound:
        print "Creating new position for %s on %s." % (adjustment_user.username, contract.ticker)
        position = models.Position(adjustment_user, contract)
        position.reference_price = 0
        session.add(position)
        return position

# Go through journal entries
journals = session.query(models.Journal).all()
for journal in journals:
    if not journal.audit:
        print "Error in Journal:\n%s" % journal
        # Make sure we don't do any adjustments if there is a basic problem like this
        adjust = False

# Go through positions
for position in positions:
    contract = position.contract
    position_calculated, calculated_timestamp = util.position_calculated(position, session)
    difference = position.position - position_calculated
    if difference != 0:
        # Mention problem
        print "Audit failure for %s" % position
        timestamp = position.position_cp_timestamp or util.timestamp_to_dt(0)
        for posting in position.user.postings:
            if posting.contract_id == contract.id and posting.journal.timestamp > timestamp:
                print "\t%s" % posting

        # Run an adjustment
        if adjust:
            position.position = position_calculated
            position.position_checkpoint = position.position
            position.position_cp_timestamp = calculated_timestamp

            session.add(position)
            session.commit()
            print "Updated Position: %s" % position




