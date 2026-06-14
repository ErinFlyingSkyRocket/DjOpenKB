"""DjOpenKB test entry point.

The real regression/security tests live under ``kb/testsuite/``.
This file is kept so Django still discovers the test package when you run
``python manage.py test kb``.

Linux / Docker Compose commands
===============================

Go to the project folder first:

    cd /opt/DjOpenKB

Recommended full validation before running tests:

    sudo docker compose exec web python manage.py check
    sudo docker compose exec web python manage.py makemigrations --check --dry-run

Run the full DjOpenKB test suite:

    sudo docker compose exec web python manage.py test kb --keepdb --verbosity=2

Run without reusing the test database:

    sudo docker compose exec web python manage.py test kb --verbosity=2

Run one test module:

    sudo docker compose exec web python manage.py test kb.testsuite.test_roles_matrix --keepdb --verbosity=2

Run one test class:

    sudo docker compose exec web python manage.py test kb.testsuite.test_article_workflow.ArticleReviewWorkflowTests --keepdb --verbosity=2

Run one exact test method:

    sudo docker compose exec web python manage.py test kb.testsuite.test_search_keywords_settings.SearchTitleKeywordOnlyTests.test_database_search_does_not_match_body_only_text --keepdb --verbosity=2

The tests do not run automatically during:

    sudo docker compose up -d --build

They only run when you manually call ``manage.py test``.
"""
