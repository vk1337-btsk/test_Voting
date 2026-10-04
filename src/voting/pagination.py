from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    """Пагинация по номеру страницы с настраиваемым размером"""

    page_size_query_param = "page_size"
    page_size = 20
    max_page_size = 100
