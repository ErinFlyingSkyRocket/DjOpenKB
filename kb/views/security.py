from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def robots_txt(request):
    """Return a restrictive robots.txt for the private Knowledge Repository.

    robots.txt is only an instruction to cooperative crawlers. Real protection
    remains the application login, MFA, role checks, and admin allowlisting.
    """
    body = "User-agent: *\nDisallow: /\n"
    response = HttpResponse(body, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response
