import json
from datetime import date

from django.db.models import ExpressionWrapper, F, IntegerField
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.authentication import JWTAuthentication

from core.models import Event, UserProfile, Subscription, WishList
from core.serializers import ListUpdateEventSerializer, EventSerializer
from utils.common import api_error_response, api_success_response
from rest_framework.authentication import get_authorization_header
from utils.helper import send_email_sms_and_notification
from eon_backend.settings import SECRET_KEY
import jwt


class EventViewSet(ModelViewSet):
    authentication_classes = (JWTAuthentication,)
    permission_classes = (IsAuthenticated,)
    queryset = Event.objects.filter(is_active=True).select_related('type').annotate(event_type=F('type__type'))
    serializer_class = ListUpdateEventSerializer

    def list(self, request, *args, **kwargs):
        """
        Function to give list of Events based on different filter parameters
        :param request: contain the query type and it's value
        :return: Response contains complete list of events after the query
        """
        location = request.GET.get("location", None)
        event_type = request.GET.get("event_type", None)
        start_date = request.GET.get("start_date", None)
        end_date = request.GET.get("end_date", None)
        event_created_by = request.GET.get("event_created_by", False)
        is_wishlisted = request.GET.get('is_wishlisted', False)

        token = get_authorization_header(request).split()[1]
        payload = jwt.decode(token, SECRET_KEY)
        user_id = payload['user_id']

        if is_wishlisted == 'True':
            try:
                event_ids = WishList.objects.filter(user=user_id).values_list('event__id', flat=True)
                self.queryset = self.queryset.filter(id__in=event_ids)
            except Exception as err:
                return api_error_response(message="Some internal error coming in fetching the wishlist", status=400)
        today = date.today()
        self.queryset.filter(date__lt=str(today)).update(is_cancelled=True)
        self.queryset = self.queryset.filter(date__gte=str(today))

        if location:
            self.queryset = self.queryset.filter(location__iexact=location)
        if event_created_by == 'True':
            self.queryset = self.queryset.filter(event_created_by=user_id)
        if event_type:
            self.queryset = self.queryset.filter(type=event_type)
        if start_date and end_date:
            self.queryset = self.queryset.filter(date__range=[start_date, end_date])
        if len(self.queryset) > 1:
            self.queryset = self.queryset.annotate(diff=ExpressionWrapper(
                F('sold_tickets') * 100000 / F('no_of_tickets'), output_field=IntegerField()))
            self.queryset = self.queryset.order_by('-diff')

        try:
            user_logged_in = user_id
            user_role = UserProfile.objects.get(user_id=user_logged_in).role.role
        except Exception as err:
            return api_error_response(message="can't access to login user id to fetch data", status=400)
        if user_role == 'subscriber':
            is_subscriber = True
        else:
            is_subscriber = False

        data = []

        for curr_event in self.queryset:
            response_obj = {"id": curr_event.id, "name": curr_event.name,
                            "date": curr_event.date, "time": curr_event.time,
                            "location": curr_event.location, "event_type": curr_event.type.id,
                            "description": curr_event.description,
                            "no_of_tickets": curr_event.no_of_tickets,
                            "sold_tickets": curr_event.sold_tickets,
                            "subscription_fee": curr_event.subscription_fee,
                            "images": curr_event.images,
                            "external_links": curr_event.external_links
                            }
            if is_subscriber:
                try:
                    Subscription.objects.get(user_id=user_logged_in, event_id=curr_event.id)
                    response_obj['is_subscribed'] = True
                except Subscription.DoesNotExist:
                    response_obj['is_subscribed'] = False

                try:
                    WishList.objects.get(user_id=user_logged_in, event_id=curr_event.id)
                    response_obj['is_wishlisted'] = True
                except WishList.DoesNotExist:
                    response_obj['is_wishlisted'] = False
            data.append(response_obj)

        return api_success_response(message="List of events", data=data)

    def create(self, request, *args, **kwargs):
        self.serializer_class = EventSerializer
        data = json.dumps(request.data)
        print(data)
        # TODO: After creating one event, that person deleted the event. After that if he tries to create same event
        # then it is given error. As we are just changing the is_active field. Handle this scenario.
        return super(EventViewSet, self).create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        token = get_authorization_header(request).split()[1]
        payload = jwt.decode(token, SECRET_KEY)
        user_id = payload['user_id']

        try:
            user_logged_in = user_id
            user_role = UserProfile.objects.get(user_id=user_logged_in).role.role
        except Exception as err:
            return api_error_response(message="Something went wrong", status=500)
        if user_role == 'subscriber':
            is_subscriber = True
        else:
            is_subscriber = False

        event_id = int(kwargs.get('pk'))
        try:
            curr_event = self.queryset.get(id=event_id)
        except Event.DoesNotExist:
            return api_error_response(message="Given event_id {} does not exist".format(event_id))

        if not is_subscriber:
            # TODO: Invitation list should not be visible to the organiser who doesn't create that event.
            # Currently it is visible for other organisers
            data = []
            invitee_list = curr_event.invitation_set.filter(is_active=True)
            invitee_data = []
            for invited in invitee_list:
                response_obj = {'invitation_id': invited.id, 'email': invited.email}
                if invited.user is not None:
                    try:
                        user_profile = UserProfile.objects.get(user=invited.user.id)
                        response_obj['user'] = {'user_id': invited.user.id, 'name': user_profile.name,
                                                'contact_number': user_profile.contact_number,
                                                'address': user_profile.address,
                                                'organization': user_profile.organization}
                    except Exception:
                        pass
                response_obj['event'] = {'event_id': invited.event.id, 'event_name': invited.event.name}
                response_obj['discount_percentage'] = invited.discount_percentage
                invitee_data.append(response_obj)
            data.append({"id": curr_event.id, "name": curr_event.name,
                         "date": curr_event.date, "time": curr_event.time,
                         "location": curr_event.location, "event_type": curr_event.type.id,
                         "description": curr_event.description,
                         "no_of_tickets": curr_event.no_of_tickets,
                         "sold_tickets": curr_event.sold_tickets,
                         "subscription_fee": curr_event.subscription_fee,
                         "images": curr_event.images,
                         "external_links": curr_event.external_links,
                         "invitee_list": invitee_data
                         })

            return api_success_response(message="event detail", data=data, status=200)
        else:
            try:
                subscription_obj = Subscription.objects.get(user_id=user_logged_in, event_id=curr_event.id)
                data = {"event_id": curr_event.id, "event_name": curr_event.name,
                        "date": curr_event.date, "time": curr_event.time,
                        "location": curr_event.location, "event_type": curr_event.type.id,
                        "description": curr_event.description,
                        "subscription_fee": curr_event.subscription_fee,
                        "images": curr_event.images,
                        "external_links": curr_event.external_links,
                        "subscription_details": {
                            "is_subscribed": is_subscriber,
                            "id": subscription_obj.id,
                            "no_of_tickets_bought": int(subscription_obj.no_of_tickets),
                            "amount_paid": subscription_obj.payment.total_amount,
                            "discount_given": subscription_obj.payment.discount_amount,
                            "discount_percentage": (subscription_obj.payment.discount_amount /
                                                    subscription_obj.payment.amount) * 100
                        }
                        }
                # TODO: One person can have multiple subscription for one event. So you should send the by summing all
                # the subscription (Buying new tickets/Cancelling some tickets)
            except Subscription.DoesNotExist:
                data = {"event_id": curr_event.id, "event_name": curr_event.name,
                        "date": curr_event.date, "time": curr_event.time,
                        "location": curr_event.location, "event_type": curr_event.type.id,
                        "description": curr_event.description,
                        "subscription_fee": curr_event.subscription_fee,
                        "images": curr_event.images,
                        "external_links": curr_event.external_links,
                        "subscription_details": []}
            return api_success_response(message="Event {} details for {} user".format(curr_event.name, user_logged_in),
                                        data=data, status=200)

    def destroy(self, request, *args, **kwargs):
        event_id = int(kwargs.get('pk'))
        # How to take message from the payload?
        message = ""
        try:
            self.queryset.get(id=event_id)
        except Event.DoesNotExist:
            return api_error_response(message="Given event {} does not exist".format(event_id))
        try:
            user_obj = Subscription.objects.select_related('user').annotate(
                email=F('user__email')).values("email", "id")
            email_ids = [_["email"] for _ in user_obj]
            user_ids = [_["id"] for _ in user_obj]
        except Subscription.DoesNotExist:
            email_ids = []
            user_ids = []
        self.queryset.filter(id=event_id).update(is_active=False)
        send_email_sms_and_notification(action_name="event_deleted",
                                        email_ids=email_ids,
                                        message=message,
                                        user_ids=user_ids)
        return api_success_response(message="Event successfully deleted", status=200)

# TODO: There is no event update API.
