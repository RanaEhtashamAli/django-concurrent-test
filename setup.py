from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

setup(
    name="django-concurrent-test",
    version="1.0.0",
    author="Django Concurrent Test Team",
    author_email="dev@example.com",
    description="Zero-config parallel testing for Django with secure database templating",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/example/django-concurrent-test",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Framework :: Django",
        "Framework :: Django :: 3.2",
        "Framework :: Django :: 4.0",
        "Framework :: Django :: 4.1",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.0",
        "Topic :: Software Development :: Testing",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=[
        "Django>=3.2,<5.1",
        "psycopg2-binary>=2.9.0; sys_platform != 'win32'",
        "psycopg2>=2.9.0; sys_platform == 'win32'",
        "mysqlclient>=2.1.0",
        "pytest>=7.0.0",
        "pytest-django>=4.5.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "pytest-xdist>=3.0.0",
            "black>=22.0.0",
            "flake8>=5.0.0",
            "mypy>=1.0.0",
        ],
        "benchmark": [
            "pytest-benchmark>=4.0.0",
        ],
    },
    entry_points={
        "pytest11": [
            "django_concurrent_test = django_concurrent_test.pytest_plugin",
        ],
    },
    include_package_data=True,
    zip_safe=False,
) 