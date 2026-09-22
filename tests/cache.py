from django_redis.client import DefaultClient
from fakeredis import FakeRedis, FakeServer

_server = FakeServer()


class IntegrationRedis(FakeRedis):
    # FakeRedis lacks server INFO; AA checks the Redis version at startup.
    def info(self, *args, **kwargs):
        return {"redis_version": "7.4.0"}


class FakeRedisClient(DefaultClient):
    def connect(self, index=0):
        return IntegrationRedis(server=_server)
