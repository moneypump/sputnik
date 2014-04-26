#!/usr/bin/env python
# Copyright (c) 2014, Mimetic Markets, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1. Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
# IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import sys
from pprint import pprint

from twisted.python import log
from twisted.internet import reactor, ssl, defer
import logging

from autobahn.twisted.websocket import connectWS
from autobahn.wamp1.protocol import WampClientFactory, WampCraClientProtocol, WampCraProtocol
from datetime import datetime, timedelta
import random
import string
import Crypto.Random.random
from ConfigParser import ConfigParser
from os import path

class TradingBot(WampCraClientProtocol):
    """
   Authenticated WAMP client using WAMP-Challenge-Response-Authentication ("WAMP-CRA").

   """

    def __init__(self):
        self.markets = {}
        self.orders = {}
        self.last_internal_id = 0
        self.chats = []

    def action(self):
        '''
        overwrite me
        '''
        return True

    def startAutomation(self):
        pass

    def startAutomationAfterAuth(self):
        pass

    def my_call(self, method_name, *args):
        log.msg("Calling %s with args=%s" % (method_name, args), logLevel=logging.DEBUG)
        d = self.call(self.factory.url + "/rpc/" + method_name, *args)
        def onSuccess(result):
            if len(result) != 2:
                log.warn("RPC Protocol error in %s" % method_name)
                return defer.succeed(result)
            if result[0]:
                return defer.succeed(result[1])
            else:
                return defer.fail(result[1])

        d.addCallbacks(onSuccess, self.onRpcFailure)
        return d

    def subscribe(self, topic, handler):
        log.msg("subscribing to %s" % topic, logLevel=logging.DEBUG)
        WampCraClientProtocol.subscribe(self, self.factory.url + "/feeds/%s" % topic, handler)

    def authenticate(self):
        [self.username, self.password] = self.factory.username_password
        d = WampCraClientProtocol.authenticate(self,
                                               authKey=self.username,
                                               authExtra=None,
                                               authSecret=self.password)

        d.addCallbacks(self.onAuthSuccess, self.onAuthError)

    """
    Utility functions
    """

    def price_to_wire(self, ticker, price):
        if self.markets[ticker]['contract_type'] == "prediction":
            price = price * self.markets[ticker]['denominator']
        else:
            price = price * self.markets[self.markets[ticker]['denominated_contract_ticker']]['denominator']

        return price - price % self.markets[ticker]['tick_size']

    def price_from_wire(self, ticker, price):
        if self.markets[ticker]['contract_type'] == "prediction":
            return float(price) / self.markets[ticker]['denominator']
        else:
            return float(price) / (self.markets[self.markets[ticker]['denominated_contract_ticker']]['denominator'] *
                            self.markets[ticker]['denominator'])

    def quantity_from_wire(self, ticker, quantity):
        if self.markets[ticker]['contract_type'] == "prediction":
            return quantity
        elif self.markets[ticker]['contract_type'] == "cash":
            return float(quantity) / self.markets[ticker]['denominator']
        else:
            return float(quantity) / self.markets[self.markets[ticker]['payout_contract_ticker']]['denominator']

    def quantity_to_wire(self, ticker, quantity):
        if self.markets[ticker]['contract_type'] == "prediction":
            return quantity
        elif self.markets[ticker]['contract_type'] == "cash":
            return quantity * self.markets[ticker]['denominator']
        else:
            quantity = quantity * self.markets[self.markets[ticker]['payout_contract_ticker']]['denominator']
            return quantity - quantity % self.markets[ticker]['lot_size']


    """
    reactive events - on* 
    """

    def onSessionOpen(self):
        self.getMarkets()
        self.subChat()
        self.startAutomation()

    def onClose(self, wasClean, code, reason):
        reactor.stop()

    def onAuthSuccess(self, permissions):
        print "Authentication Success!", permissions
        self.subOrders()
        self.subFills()
        self.subTransactions()
        self.getOpenOrders()

        self.startAutomationAfterAuth()

    def onAuthError(self, e):
        uri, desc, details = e.value.args
        print "Authentication Error!", uri, desc, details

    def onMarkets(self, event):
        pprint(event)
        self.markets = event
        for ticker, contract in self.markets.iteritems():
            if contract['contract_type'] != "cash":
                self.getOrderBook(ticker)
                self.subBook(ticker)
                self.subTrades(ticker)
                self.subSafePrices(ticker)
                self.subOHLCV(ticker)
        return event

    def onOpenOrders(self, event):
        pprint(event)
        for id, order in event.iteritems():
            self.orders[int(id)] = order

    def onOrder(self, topicUri, order):
        """
        overwrite me
        """
        id = order['id']
        if id in self.orders and (order['is_cancelled'] or order['quantity_left'] == 0):
            del self.orders[id]
        else:
            if 'quantity' in order:
                # Try to find it in internal orders
                for search_id, search_order in self.orders.items():
                    if isinstance(search_id, basestring) and search_id.startswith('internal_'):
                        if (order['quantity'] == search_order['quantity'] and
                            order['side'] == search_order['side'] and
                            order['contract'] == search_order['contract'] and
                            order['price'] == search_order['price']):
                            del self.orders[search_id]
                            self.orders[id] = order

        pprint(["Order", topicUri, order])

    def onFill(self, topicUri, event):
        """
        overwrite me
        """
        pprint(["Fill", topicUri, event])

    def onTransaction(self, topicUri, event):
        """
        overwrite me
        """
        pprint(["Transaction", topicUri, event])

    def onChat(self, topicUri, event):
        """
        overwrite me
        """
        self.chats.append(event)
        pprint(["Chat", topicUri, event])

    def onChatHistory(self, event):
        self.chats = event
        pprint(["Chat History", event])

    def onPlaceOrder(self, event):
        """
        overwrite me
        """
        pprint(event)

    def onOHLCV(self, topicUri, event):
        pprint(event)

    def onOHLCVHistory(self, event):
        pprint(event)

    def onError(self, message):
        pprint(["Error", message.value])

    def onRpcFailure(self, event):
        pprint(["RpcFailure", event.value.args])

    def onAudit(self, event):
        pprint(event)

    def onMakeAccount(self, event):
        pprint(event)

    def onSupportNonce(self, event):
        pprint(event)

    def onTransactionHistory(self, event):
        pprint(event)

    """
    Feed handlers
    """

    def onBook(self, topicUri, event):
        """
        overwrite me
        """
        pprint(["Book: ", topicUri, event])
        self.markets[event['contract']]['bids'] = event['bids']
        self.markets[event['contract']]['asks'] = event['asks']

    def onTrade(self, topicUri, event):
        """
        overwrite me
        """
        pprint(["Trade: ", topicUri, event])

    def onSafePrice(self, topicUri, event):
        """
        overwrite me
        """
        pprint(["SafePrice", topicUri, event])


    """
    Public Subscriptions
    """
    def subOHLCV(self, ticker):
        uri = "ohlcv#%s" % ticker
        self.subscribe(uri, self.onOHLCV)
        print "subscribed to: ", uri

    def subBook(self, ticker):
        uri = "book#%s" % ticker
        self.subscribe(uri, self.onBook)
        print 'subscribed to: ', uri

    def subTrades(self, ticker):
        uri = "trades#%s" % ticker
        self.subscribe(uri, self.onTrade)
        print 'subscribed to: ', uri

    def subSafePrices(self, ticker):
        uri = "safe_prices#%s" % ticker
        self.subscribe(uri, self.onSafePrice)
        print 'subscribed to: ', uri

    def subChat(self):
        uri = "chat"
        self.subscribe(uri, self.onChat)
        print 'subscribe to: ', uri

    """
    Private Subscriptions
    """
    def subOrders(self):
        uri = "orders#%s" % self.username
        self.subscribe(uri, self.onOrder)
        print 'subscribed to: ', uri

    def subFills(self):
        uri = "fills#%s" % self.username
        self.subscribe(uri, self.onFill)
        print 'subscribed to: ', uri

    def subTransactions(self):
        uri = "transactions#%s" % self.username
        self.subscribe(uri, self.onTransaction)
        print 'subscribed to: ', uri

    """
    Public RPC Calls
    """


    def getTradeHistory(self, ticker):
        d = self.my_call("get_trade_history", ticker)
        d.addCallbacks(pprint, self.onError)

    def getChatHistory(self):
        d = self.my_call("get_chat_history")
        d.addCallbacks(self.onChatHistory, self.onError)

    def getMarkets(self):
        d = self.my_call("get_markets")
        d.addCallbacks(self.onMarkets, self.onError)

    def getOrderBook(self, ticker):
        d = self.my_call("get_order_book", ticker)
        d.addCallbacks(lambda x: self.onBook("get_order_book", x), self.onError)

    def getAudit(self):
        d = self.my_call("get_audit")
        d.addCallbacks(self.onAudit, self.onError)

    def getOHLCVHistory(self, ticker, period="day", start_datetime=datetime.now()-timedelta(days=2), end_datetime=datetime.now()):
        epoch = datetime.utcfromtimestamp(0)
        start_timestamp = int((start_datetime - epoch).total_seconds() * 1e6)
        end_timestamp = int((end_datetime - epoch).total_seconds() * 1e6)

        d = self.my_call("get_ohlcv_history", ticker, period, start_timestamp, end_timestamp)
        d.addCallbacks(self.onOHLCVHistory, self.onError)

    def makeAccount(self, username, password, email, nickname):
        alphabet = string.digits + string.lowercase
        num = Crypto.Random.random.getrandbits(64)
        salt = ""
        while num != 0:
            num, i = divmod(num, len(alphabet))
            salt = alphabet[i] + salt
        extra = {"salt":salt, "keylen":32, "iterations":1000}
        password_hash = WampCraProtocol.deriveKey(password, extra)
        d = self.my_call("make_account", username, password_hash, salt, email, nickname)
        d.addCallbacks(self.onMakeAccount, self.onError)

    def getResetToken(self, username):
        d = self.my_call("get_reset_token", username)
        d.addCallbacks(pprint, self.onError)

    """
    Private RPC Calls
    """

    def getPositions(self):
        d = self.my_call("get_positions")
        d.addCallbacks(pprint, self.onError)

    def getCurrentAddress(self):
        d = self.my_call("get_current_address")
        d.addCallbacks(pprint, self.onError)

    def getNewAddress(self):
        d = self.my_call("get_new_address")
        d.addCallbacks(pprint, self.onError)

    def getOpenOrders(self):
        # store cache of open orders update asynchronously
        d = self.my_call("get_open_orders")
        d.addCallbacks(self.onOpenOrders, self.onError)

    def getTransactionHistory(self, start_datetime=datetime.now()-timedelta(days=2), end_datetime=datetime.now()):
        epoch = datetime.utcfromtimestamp(0)
        start_timestamp = int((start_datetime - epoch).total_seconds() * 1e6)
        end_timestamp = int((end_datetime - epoch).total_seconds() * 1e6)

        d = self.my_call("get_transaction_history", start_timestamp, end_timestamp)
        d.addCallbacks(self.onTransactionHistory, self.onError)

    def requestSupportNonce(self, type='Compliance'):
        d = self.my_call("request_support_nonce", type)
        d.addCallbacks(self.onSupportNonce, self.onError)

    def placeOrder(self, ticker, quantity, price, side):
        ord= {}
        ord['contract'] = ticker
        ord['quantity'] = quantity
        ord['price'] = price
        ord['side'] = side
        d = self.my_call("place_order", ord)
        d.addCallbacks(self.onPlaceOrder, self.onError)

        self.last_internal_id += 1
        ord['quantity_left'] = ord['quantity']
        ord['is_cancelled'] = False
        self.orders['internal_%d' % self.last_internal_id] = ord

    def chat(self, message):
        print "chatting: ", message
        self.publish(self.factory.url + "/feeds/chat", message)

    def cancelOrder(self, id):
        """
        cancels an order by its id.
        :param id: order id
        """
        if isinstance(id, basestring) and id.startswith('internal_'):
            print "can't cancel internal order: %s" % id

        print "cancel order: %s" % id
        d = self.my_call("cancel_order", id)
        d.addCallbacks(pprint, self.onError)
        del self.orders[id]

class BasicBot(TradingBot):
    def onMakeAccount(self, event):
        TradingBot.onMakeAccount(self, event)
        self.authenticate()

    def startAutomation(self):
        # Test the audit
        self.getAudit()

        # Now make an account
        self.username = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        self.password = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        self.makeAccount(self.username, self.password, "test@m2.io", "Test User")

    def startAutomationAfterAuth(self):
        self.getTransactionHistory()
        self.requestSupportNonce()

class BotFactory(WampClientFactory):
    def __init__(self, url, debugWamp=False, username_password=(None, None)):
        WampClientFactory.__init__(self, url, debugWamp=debugWamp)
        self.username_password = username_password

if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s - %(levelname)s - %(funcName)s() %(lineno)d:\t %(message)s', level=logging.DEBUG)

    if len(sys.argv) > 1 and sys.argv[1] == 'debug':
        debug = True
    else:
        debug = False

    log.startLogging(sys.stdout)
    config = ConfigParser()
    config_file = path.abspath(path.join(path.dirname(__file__),
            "./client.ini"))
    config.read(config_file)

    base_uri = config.get("client", "base_uri")
    username = config.get("client", "username")
    password = config.get("client", "password")

    factory = BotFactory(base_uri, debugWamp=debug, username_password=(username, password))
    factory.protocol = BasicBot

    # null -> ....
    if factory.isSecure:
        contextFactory = ssl.ClientContextFactory()
    else:
        contextFactory = None

    connectWS(factory, contextFactory)
    reactor.run()
