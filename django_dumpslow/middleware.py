# -*- coding: utf-8 -*-
#
# django-dumpslow -- Django application to log and summarize slow requests
#                    <http://chris-lamb.co.uk/projects/django-dumpslow>
#
# Copyright © 2009-2010 Chris Lamb <chris@chris-lamb.co.uk>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.

import time
import redis
import threading

from django.conf import settings
from django.core.mail import mail_admins

from django_dumpslow.utils import parse_interval

class LogLongRequestMiddleware(object):
    def __init__(self):
        self.local = threading.local()

    def process_view(self, request, callback, callback_args, callback_kwargs):
        view = '%s.' % callback.__module__

        try:
            view += callback.__name__
        except (AttributeError, TypeError):
            # Some view functions (eg. class-based views) do not have a
            # __name__ attribute; try and get the name of its class
            view += callback.__class__.__name__

        self.local.view = view
        self.local.start_time = time.time()

    def process_response(self, request, response):
        try:
            view = self.local.view
            time_taken = time.time() - self.local.start_time
        except AttributeError:
            # If, for whatever reason, the variables are not available, don't
            # do anything else.
            return response

        if time_taken < getattr(settings, 'DUMPSLOW_LONG_REQUEST_TIME', 1):
            return response

        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
        )

        client.zadd(
            getattr(settings, 'DUMPSLOW_REDIS_KEY', 'dumpslow'),
            '%s\n%.3f' % (view, time_taken),
            self.local.start_time,
        )

        # Clean up old values

        delete_after = parse_interval(
            getattr(settings, 'DUMPSLOW_DELETE_AFTER', '4w'),
        )

        client.zremrangebyscore(
            getattr(settings, 'DUMPSLOW_REDIS_KEY', 'dumpslow'),
            0,
            int(time.time()) - delete_after,
        )

        # If it was really slow, email admins. Disabled by default.
        email_threshold = getattr(settings, 'DUMPSLOW_EMAIL_REQUEST_TIME', -1)
        if email_threshold > -1 and time_taken > email_threshold:
            include = True
            for email_exclude in getattr(settings, 'DUMPSLOW_EMAIL_EXCLUDES', []):
                if request.path.startswith(email_exclude):
                    include = False
                    break

            if include:
                subject = "SLOW PAGE: %s" % request.path
                message = "This page took %2.2f seconds to render for %s, which is over the threshold of %s.\n\n%s" % (time_taken, request.user, email_threshold, str(request)) 
                try:
                    mail_admins(subject, message)
                except Exception as e:
                    # Ignore any errors sending mail in production
                    if settings.DEBUG:
                        raise e
                    else:
                        pass

        return response
