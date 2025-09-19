from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin


class LiveConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    pass