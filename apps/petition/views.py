from rest_framework import generics, permissions

from apps.petition.serializers import PetitionSerializer


class PetitionCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PetitionSerializer
