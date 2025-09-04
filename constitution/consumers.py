from djangochannelsrestframework.generics import GenericAsyncAPIConsumer


class ConstitutionConsumer(GenericAsyncAPIConsumer):
    serializer_class = BallotSerializer
    queryset = Ballot.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.ballot_activity.subscribe()
        await self.option_activity.subscribe()