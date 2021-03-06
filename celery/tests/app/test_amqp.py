from __future__ import absolute_import

from kombu import Exchange, Queue
from mock import Mock

from celery.app.amqp import Queues, TaskPublisher
from celery.tests.case import AppCase


class test_TaskProducer(AppCase):

    def test__exit__(self):
        publisher = self.app.amqp.TaskProducer(self.app.connection())
        publisher.release = Mock()
        with publisher:
            pass
        publisher.release.assert_called_with()

    def test_declare(self):
        publisher = self.app.amqp.TaskProducer(self.app.connection())
        publisher.exchange.name = 'foo'
        publisher.declare()
        publisher.exchange.name = None
        publisher.declare()

    def test_retry_policy(self):
        prod = self.app.amqp.TaskProducer(Mock())
        prod.channel.connection.client.declared_entities = set()
        prod.publish_task('tasks.add', (2, 2), {},
                          retry_policy={'frobulate': 32.4})

    def test_publish_no_retry(self):
        prod = self.app.amqp.TaskProducer(Mock())
        prod.channel.connection.client.declared_entities = set()
        prod.publish_task('tasks.add', (2, 2), {}, retry=False, chord=123)
        self.assertFalse(prod.connection.ensure.call_count)

    def test_publish_custom_queue(self):
        prod = self.app.amqp.TaskProducer(Mock())
        self.app.amqp.queues['some_queue'] = Queue(
            'xxx', Exchange('yyy'), 'zzz',
        )
        prod.channel.connection.client.declared_entities = set()
        prod.publish = Mock()
        prod.publish_task('tasks.add', (8, 8), {}, retry=False,
                          queue='some_queue')
        self.assertEqual(prod.publish.call_args[1]['exchange'], 'yyy')
        self.assertEqual(prod.publish.call_args[1]['routing_key'], 'zzz')

    def test_event_dispatcher(self):
        prod = self.app.amqp.TaskProducer(Mock())
        self.assertTrue(prod.event_dispatcher)
        self.assertFalse(prod.event_dispatcher.enabled)


class test_TaskConsumer(AppCase):

    def test_accept_content(self):
        with self.app.pool.acquire(block=True) as conn:
            self.app.conf.CELERY_ACCEPT_CONTENT = ['application/json']
            self.assertEqual(
                self.app.amqp.TaskConsumer(conn).accept,
                set(['application/json'])
            )
            self.assertEqual(
                self.app.amqp.TaskConsumer(conn, accept=['json']).accept,
                set(['application/json']),
            )


class test_compat_TaskPublisher(AppCase):

    def test_compat_exchange_is_string(self):
        producer = TaskPublisher(exchange='foo', app=self.app)
        self.assertIsInstance(producer.exchange, Exchange)
        self.assertEqual(producer.exchange.name, 'foo')
        self.assertEqual(producer.exchange.type, 'direct')
        producer = TaskPublisher(exchange='foo', exchange_type='topic',
                                 app=self.app)
        self.assertEqual(producer.exchange.type, 'topic')

    def test_compat_exchange_is_Exchange(self):
        producer = TaskPublisher(exchange=Exchange('foo'))
        self.assertEqual(producer.exchange.name, 'foo')


class test_PublisherPool(AppCase):

    def test_setup_nolimit(self):
        L = self.app.conf.BROKER_POOL_LIMIT
        self.app.conf.BROKER_POOL_LIMIT = None
        try:
            delattr(self.app, '_pool')
        except AttributeError:
            pass
        self.app.amqp._producer_pool = None
        try:
            pool = self.app.amqp.producer_pool
            self.assertEqual(pool.limit, self.app.pool.limit)
            self.assertFalse(pool._resource.queue)

            r1 = pool.acquire()
            r2 = pool.acquire()
            r1.release()
            r2.release()
            r1 = pool.acquire()
            r2 = pool.acquire()
        finally:
            self.app.conf.BROKER_POOL_LIMIT = L

    def test_setup(self):
        L = self.app.conf.BROKER_POOL_LIMIT
        self.app.conf.BROKER_POOL_LIMIT = 2
        try:
            delattr(self.app, '_pool')
        except AttributeError:
            pass
        self.app.amqp._producer_pool = None
        try:
            pool = self.app.amqp.producer_pool
            self.assertEqual(pool.limit, self.app.pool.limit)
            self.assertTrue(pool._resource.queue)

            p1 = r1 = pool.acquire()
            p2 = r2 = pool.acquire()
            r1.release()
            r2.release()
            r1 = pool.acquire()
            r2 = pool.acquire()
            self.assertIs(p2, r1)
            self.assertIs(p1, r2)
            r1.release()
            r2.release()
        finally:
            self.app.conf.BROKER_POOL_LIMIT = L


class test_Queues(AppCase):

    def test_queues_format(self):
        prev, self.app.amqp.queues._consume_from = (
            self.app.amqp.queues._consume_from, {})
        try:
            self.assertEqual(self.app.amqp.queues.format(), '')
        finally:
            self.app.amqp.queues._consume_from = prev

    def test_with_defaults(self):
        self.assertEqual(Queues(None), {})

    def test_add(self):
        q = Queues()
        q.add('foo', exchange='ex', routing_key='rk')
        self.assertIn('foo', q)
        self.assertIsInstance(q['foo'], Queue)
        self.assertEqual(q['foo'].routing_key, 'rk')

    def test_with_ha_policy(self):
        qn = Queues(ha_policy=None, create_missing=False)
        qn.add('xyz')
        self.assertIsNone(qn['xyz'].queue_arguments)

        qn.add('xyx', queue_arguments={'x-foo': 'bar'})
        self.assertEqual(qn['xyx'].queue_arguments, {'x-foo': 'bar'})

        q = Queues(ha_policy='all', create_missing=False)
        q.add(Queue('foo'))
        self.assertEqual(q['foo'].queue_arguments, {'x-ha-policy': 'all'})

        qq = Queue('xyx2', queue_arguments={'x-foo': 'bari'})
        q.add(qq)
        self.assertEqual(q['xyx2'].queue_arguments, {
            'x-ha-policy': 'all',
            'x-foo': 'bari',
        })

        q2 = Queues(ha_policy=['A', 'B', 'C'], create_missing=False)
        q2.add(Queue('foo'))
        self.assertEqual(q2['foo'].queue_arguments, {
            'x-ha-policy': 'nodes',
            'x-ha-policy-params': ['A', 'B', 'C'],
        })

    def test_select_add(self):
        q = Queues()
        q.select_subset(['foo', 'bar'])
        q.select_add('baz')
        self.assertItemsEqual(q._consume_from.keys(), ['foo', 'bar', 'baz'])

    def test_select_remove(self):
        q = Queues()
        q.select_subset(['foo', 'bar'])
        q.select_remove('bar')
        self.assertItemsEqual(q._consume_from.keys(), ['foo'])

    def test_with_ha_policy_compat(self):
        q = Queues(ha_policy='all')
        q.add('bar')
        self.assertEqual(q['bar'].queue_arguments, {'x-ha-policy': 'all'})

    def test_add_default_exchange(self):
        ex = Exchange('fff', 'fanout')
        q = Queues(default_exchange=ex)
        q.add(Queue('foo'))
        self.assertEqual(q['foo'].exchange, ex)

    def test_alias(self):
        q = Queues()
        q.add(Queue('foo', alias='barfoo'))
        self.assertIs(q['barfoo'], q['foo'])
