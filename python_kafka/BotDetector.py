import json
import logging
import os
import random
import uuid

import pymongo
from confluent_kafka import Consumer, Producer
import sys
# from dendritic_cell_algorithm import dc_algorithm
from dendritic_cell_algorithm.antigen import Antigen
from dendritic_cell_algorithm.dendritic_cell import DendriticCell
from dendritic_cell_algorithm.dendritic_cell_algorithm import random_in_bounds
from dendritic_cell_algorithm.signal_generator import Signals


def delivery_report(err, msg):
    """ Called once for each message produced to indicate delivery result.
        Triggered by poll() or flush(). """
    if err is not None:
        print('BotDetector: Message delivery failed: {}'.format(err))
        sys.stdout.flush()
    else:
        print('BotDetector: Message delivered to {} [{}]'.format(msg.topic(), msg.partition()))
        sys.stdout.flush()


def startBotDetector(consumer_servers, consumer_group_id, consumer_offset, consumer_topic, producer_servers,
                     collection_name):
    random.seed(10)
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

    if int(os.environ['USE_DATABASE_SERVICE']):
        print("use db service")
        client = pymongo.MongoClient(os.environ['DATABASE_SERVICE'], int(os.environ['DATABASE_PORT']),
                                     username=os.environ['DATABASE_USERNAME'],
                                     password=os.environ['DATABASE_PASSWORD'])
    else:
        print("don't use db service")
        client = pymongo.MongoClient(os.environ['DATABASE_URL'])

    try:
        db = client["TwitterData"]
        col1 = db[collection_name]
        request_col = db["Requests"]
    except AttributeError as error:
        print(error)

    antigen_array = []
    antigen_id_array = []
    result = {"classified_count": 0, "classified_correctly_count": 0, "time": 0}
    dc_array = []
    for i in range(50):
        dc = DendriticCell(str(i))
        dc_array.append(dc)

    dc_count = len(dc_array)

    logging.info("dc_array")
    logging.info([str(item) for item in dc_array])

    while True:
        msg = c.poll(1.0)

        if msg is None:
            continue

        if msg.error():
            print("BotDetector: Consumer error: {}".format(msg.error()))
            sys.stdout.flush()
            continue

        if msg.key().decode('utf-8') == "INFO":
            if msg.value().decode('utf-8') == "END":
                last_antigen = Antigen(str("last"), "last", 0, 400, 20, antigen_array, {})
                for dcell in dc_array[:]:
                    logging.info("expose cell {0} to antigen {1}".format(dcell.id, last_antigen.id))
                    cell, status = dcell.expose_cell(last_antigen)
                    if status == 1:
                        dc_count += 1
                        dc_array.remove(cell)
                request_col.update_one({"collection": collection_name},
                                       {'$set': {'completed': True}})
                break
            else:
                continue

        print('BotDetector: Received message: {0}  |  {1}'.format(msg.value().decode('utf-8')[:50],
                                                                  msg.key().decode('utf-8')))

        user = json.loads(msg.value())

        """new_antigen = Antigen(user["user"]["id_str"], user, user["signals"]["k"], user["signals"]["cms"],
                              10,
                              antigen_array, send_info_to_mongodb=col1)"""
        if not user["found_tweet"]["id_str"] in antigen_id_array:
            new_antigen = Antigen(uuid.uuid4(), user, user["signals"]["k"], user["signals"]["cms"],
                                  10,
                                  antigen_array, send_info_to_mongodb=col1)
            antigen_array.append(new_antigen)
            antigen_id_array.append(user["found_tweet"]["id_str"])
        else:
            print("already in array")
            continue


        for i in range(new_antigen.number_of_copies):
            cell_random = int(random_in_bounds(0, (len(dc_array) - 1)))
            logging.info("expose cell {0} to antigen {1}".format(int(dc_array[cell_random].id), int(new_antigen.id)))
            cell, status = dc_array[cell_random].expose_cell(new_antigen)

            if status == 1:
                dc_count += 1
                dc_array.remove(cell)
                dc = DendriticCell(str(dc_count))
                dc_array.append(dc)

        # col1.insert_one(user)
        # print("BotDetector: Send " + str(msg.value())[:50])

    c.close()
