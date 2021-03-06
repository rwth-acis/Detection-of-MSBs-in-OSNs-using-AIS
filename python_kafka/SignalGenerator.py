import copy
import json
import logging
import os

import tweepy
from confluent_kafka import Consumer, Producer
import sys

from dotenv import load_dotenv
from geopy import Nominatim
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from dendritic_cell_algorithm.signal_generator import remove_user_mentions, remove_urls, Signals


def delivery_report(err, msg):
    """ Called once for each message produced to indicate delivery result.
        Triggered by poll() or flush(). """
    if err is not None:
        print('SignalGenerator: Message delivery failed: {}'.format(err))
        sys.stdout.flush()
    else:
        print('SignalGenerator: Message delivered to {} [{}]'.format(msg.topic(), msg.partition()))
        sys.stdout.flush()


def startSignalGenerator(consumer_servers, consumer_group_id, consumer_offset, consumer_topic, producer_servers,
                         producer_topic,
                         consumer_key=None, consumer_secret=None, access_token=None,
                         access_token_secret=None, bearer=None):
    c = Consumer({
        'bootstrap.servers': consumer_servers,
        'group.id': consumer_group_id,
        'auto.offset.reset': consumer_offset
    })

    producer = Producer({'bootstrap.servers': producer_servers})
    server_topics = c.list_topics().topics

    if consumer_topic in server_topics:
        c.subscribe([consumer_topic])
    else:
        producer.produce(consumer_topic, key="INFO", value=("Create topic " + consumer_topic), callback=delivery_report)
        producer.flush()
        c.subscribe([consumer_topic])

    while True:
        msg = c.poll(1.0)

        if msg is None:
            continue

        if msg.error():
            print("SignalGenerator: Consumer error: {}".format(msg.error()))
            sys.stdout.flush()
            continue

        if msg.key().decode('utf-8') == "INFO":
            if msg.value().decode('utf-8') == "END":
                producer.flush()
                producer.produce(producer_topic, key="INFO", value="END", callback=delivery_report)
                producer.flush()
                break
            else:
                continue
        print('SignalGenerator: Received message: {0}  |  {1}'.format(msg.value().decode('utf-8')[:50],
                                                                      msg.key().decode('utf-8')))

        tweet = json.loads(msg.value())
        tweet["created_at"] = tweet["created_at"].replace(" +0000", "")

        if "truncated" in tweet and "retweeted_status" in tweet:
            if tweet["truncated"]:
                tweet["text"] = tweet["retweeted_status"]["full_text"]
                tweet["full_text"] = tweet["retweeted_status"]["full_text"]

        if not ("full_text" in tweet):
            tweet["full_text"] = tweet["text"]

        ##############################################################################

        if bearer is not None:
            auth = tweepy.OAuth2BearerHandler(bearer)
        else:
            auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
            auth.set_access_token(access_token, access_token_secret)

        api = tweepy.API(auth, retry_count=3, timeout=100000, wait_on_rate_limit=True)

        userObj = {}
        user = api.get_user(screen_name=tweet["user"]["screen_name"])._json
        user.pop('status', None)
        userObj["user"] = user
        userObj["found_tweet"] = tweet

        geo_locator = Nominatim(user_agent="findBots")
        if userObj["user"]["location"]:
            try:
                location = geo_locator.geocode(userObj["user"]["location"])
                logging.info(location)
                if location is None:
                    logging.info("try to add loc")
                    userObj["coordinates"] = ""
            except Exception as e:
                continue
            if location:
                logging.info("try to add loc")
                userObj["coordinates"] = [location.latitude, location.longitude]

        logging.info("sentiment!")
        analyzer = SentimentIntensityAnalyzer()
        tweet_modified = remove_user_mentions(remove_urls(copy.deepcopy(userObj["found_tweet"])))
        sentence = tweet_modified["full_text"]
        sentiment = analyzer.polarity_scores(sentence)
        logging.info(sentence)
        logging.info(sentiment['compound'])
        if sentiment['compound'] >= 0.2:
            logging.info("Positive")
            userObj["found_tweet"]["sentiment"] = "positive"

        elif sentiment['compound'] <= - 0.2:
            logging.info("Negative")
            userObj["found_tweet"]["sentiment"] = "negative"

        else:
            logging.info("Neutral")
            userObj["found_tweet"]["sentiment"] = "neutral"

        userObj["tweets"] = []
        for fulltweet in api.user_timeline(screen_name=tweet["user"]["screen_name"],
                                           # max 200 tweets
                                           count=20,
                                           include_rts=False,
                                           # Necessary to keep full_text
                                           tweet_mode='extended'
                                           ):
            tw = fulltweet._json

            logging.info("sentiment!")
            analyzer = SentimentIntensityAnalyzer()
            tweet_modified = remove_user_mentions(remove_urls(copy.deepcopy(tw)))
            sentence = tweet_modified["full_text"]
            sentiment = analyzer.polarity_scores(sentence)
            logging.info(sentence)
            logging.info(sentiment['compound'])
            if sentiment['compound'] >= 0.1:
                logging.info("Positive")
                tw["sentiment"] = "positive"

            elif sentiment['compound'] <= - 0.2:
                logging.info("Negative")
                tw["sentiment"] = "negative"

            else:
                logging.info("Neutral")
                tw["sentiment"] = "positive"
            tw.pop('user', None)
            userObj["tweets"].append(tw)

        new_signals = Signals()
        # friends_count, followers_count, verified, default_profile, default_profile_image, created_at, name,
        # screen_name, description, tweets
        new_signals.generate_signals(user["friends_count"], user["statuses_count"], user["followers_count"],
                                     user["verified"],
                                     user["default_profile"],
                                     user["default_profile_image"], user["created_at"], user["name"],
                                     user["screen_name"],
                                     user["description"],
                                     userObj["tweets"])

        logging.info(new_signals.get_parameters())
        userObj["signals"] = new_signals.get_parameters()

        ##############################################################################

        producer.produce(producer_topic, key=msg.key(), value=json.dumps(userObj), callback=delivery_report)
        producer.flush()
        print("SignalGenerator: Send " + str(json.dumps(userObj))[:50])

    c.close()
