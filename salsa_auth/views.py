import datetime
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import send_mail
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_text
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.views.generic import FormView, RedirectView

from salsa_auth.forms import SignUpForm, LoginForm
from salsa_auth.models import UserZipCode
from salsa_auth.salsa import client as salsa_client
from salsa_auth.tokens import account_activation_token


class JSONFormResponseMixin:
    def form_valid(self, form):
        context = self.get_context_data(form=form)
        return self.render_to_response(context)

    def form_invalid(self, form):
        context = self.get_context_data(form=form)
        return self.render_to_response(context)

    def render_to_response(self, context, **kwargs):
        response = {}

        errors = context['form'].errors

        if errors:
            response['redirect_url'] = None
            response['errors'] = errors
        else:
            response['redirect_url'] = getattr(self, 'redirect_url', self.request.POST['next'])

        return JsonResponse(response)


class SignUpForm(JSONFormResponseMixin, FormView):
    form_class = SignUpForm
    template_name = 'signup.html'

    def form_valid(self, form):
        user = self._make_user(form.cleaned_data)

        # TO-DO: Potentially intercept SMTP error for undeliverable mail here
        self._send_verification_email(user)

        messages.add_message(self.request,
                             messages.INFO,
                             'Thanks for signing up!',
                             extra_tags='font-weight-bold')

        messages.add_message(self.request,
                             messages.INFO,
                             'Please check your email for an activation link.')

        return super().form_valid(form)

    def _make_user(self, form_data):
        zip_code = form_data.pop('zip_code')

        user = User.objects.create(**form_data, username=str(uuid4()).split('-')[0])
        user.set_unusable_password()
        user.save()

        user_zip = UserZipCode.objects.create(user=user, zip_code=zip_code)

        return user

    def _send_verification_email(self, user):
        current_site = get_current_site(self.request)
        email_subject = 'Activate Your Account'

        uid = urlsafe_base64_encode(force_bytes(user.pk))

        # uid will be a bytestring in Django < 2.2. Cast it to a string before
        # rendering it into the email template.
        if isinstance(uid, (bytes, bytearray)):
            uid = uid.decode('utf-8')

        message = render_to_string('emails/activate_account.html', {
            'user': user,
            'domain': current_site.domain,
            'uid': uid,
            'token': account_activation_token.make_token(user),
        })
        send_mail(email_subject,
                  message,
                  getattr(settings, 'DEFAULT_FROM_EMAIL', 'testing@datamade.us'),
                  [user.email])


class LoginForm(JSONFormResponseMixin, FormView):
    form_class = LoginForm
    template_name = 'login.html'
    redirect_url = '/salsa/authenticate'

    def post(self, *args, **kwargs):
        form = self.get_form()

        if form.is_valid():
            user = salsa_client.get_supporter(form.cleaned_data['email'])

            if not user:
                error_message = (
                    '<strong>{email}</strong> is not subscribed to the BGA mailing list. Please '
                    '<a href="javascript://" class="toggle-login-signup" data-parent_modal="loginModal">sign up</a> '
                    'to access this tool.'
                )
                form.errors['email'] = [error_message.format(email=form.cleaned_data['email'])]
                return self.form_invalid(form)

            try:
                greeting_name = user['firstName']
            except KeyError:
                greeting_name = form.cleaned_data['email']

            messages.add_message(self.request,
                                 messages.INFO,
                                 'Welcome back, {}!'.format(greeting_name),
                                 extra_tags='font-weight-bold')

            return self.form_valid(form)

        return self.form_invalid(form)


class VerifyEmail(RedirectView):
    def get(self, request, uidb64, token):
        '''
        https://simpleisbetterthancomplex.com/tutorial/2016/08/24/how-to-create-one-time-link.html
        '''
        try:
            uid = force_text(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        link_valid = user is not None and account_activation_token.check_token(user, token)

        if link_valid:
            salsa_client.put_supporter(user)

            messages.add_message(self.request,
                                 messages.INFO,
                                 'Welcome back, {}!'.format(user.first_name),
                                 extra_tags='font-weight-bold')

            return redirect('salsa_auth:authenticate')

        else:
            messages.add_message(self.request,
                                 messages.ERROR,
                                 'Invalid activation link.',
                                 extra_tags='font-weight-bold')

            contact_message = (
                'Think you received this message in error? '
                '<a href="https://www.bettergov.org/contact/" target="_blank">Get in touch &raquo;</a>'
            )

            messages.add_message(self.request,
                                 messages.ERROR,
                                 contact_message)

            return redirect(settings.SALSA_AUTH_REDIRECT_LOCATION)


class Authenticate(RedirectView):
    url = settings.SALSA_AUTH_REDIRECT_LOCATION

    def get(self, *args, **kwargs):
        response = HttpResponseRedirect(self.url)

        response.set_cookie(
            settings.SALSA_AUTH_COOKIE_NAME,
            'true',
            expires=datetime.datetime.now() + datetime.timedelta(weeks=52),
            domain=settings.SALSA_AUTH_COOKIE_DOMAIN,
        )

        messages.add_message(self.request,
                             messages.INFO,
                             "We've logged you in so you can continue using the database.")

        return response
