from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer

from apps.geo.models import County, Constituency, Ward
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer


class GeoConsumer(GenericAsyncAPIConsumer):

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @action()
    def counties(self, **kwargs):
        counties = County.objects.all()
        data = CountySerializer(counties, many=True, context={'scope': self.scope}).data
        return data, 200

    @action()
    def constituencies(self, county: int, **kwargs):
        constituencies = Constituency.objects.filter(county=county)
        data = ConstituencySerializer(constituencies, many=True, context={'scope': self.scope}).data
        return data, 200

    @action()
    def wards(self, constituency: int, **kwargs):
        wards = Ward.objects.filter(constituency=constituency)
        data = WardSerializer(wards, many=True, context={'scope': self.scope}).data
        return data, 200
