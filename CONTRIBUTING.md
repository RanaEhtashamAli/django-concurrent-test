# Contributing to Django Concurrent Test

Thank you for your interest in contributing to django-concurrent-test! This document provides guidelines and information for contributors.

## 📞 Contact

- **Email**: ranaehtashamali1@gmail.com
- **Phone**: +923224712517
- **GitHub**: [@RanaEhtashamAli](https://github.com/RanaEhtashamAli)

## 🚀 Getting Started

### Prerequisites

- Python 3.9+
- Django 3.2+
- Git

### Development Setup

1. **Fork the repository**
   ```bash
   git clone https://github.com/RanaEhtashamAli/django-concurrent-test.git
   cd django-concurrent-test
   ```

2. **Install development dependencies**
   ```bash
   pip install -e ".[dev,test]"
   ```

3. **Run tests**
   ```bash
   pytest tests/
   pytest --concurrent tests/
   ```

## 🔧 Development Guidelines

### Code Style

- **Python**: Follow PEP 8 guidelines
- **Type Hints**: Use comprehensive type hints for all public functions
- **Docstrings**: Include detailed docstrings for all public APIs
- **Formatting**: Use Black for code formatting (line length: 88)
- **Imports**: Use isort for import sorting

### Testing

- **Coverage**: Maintain high test coverage (aim for >90%)
- **Test Types**: Include unit tests, integration tests, and concurrent tests
- **Database Testing**: Test with SQLite, PostgreSQL, and MySQL
- **Concurrent Testing**: Test concurrent execution scenarios

### Security

- **Environment Validation**: All security features must be tested
- **Permission Checks**: Validate database permissions and file access
- **Input Sanitization**: Sanitize all user inputs and file paths
- **Error Handling**: Graceful error handling without exposing sensitive information

## 📝 Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Follow the coding guidelines
   - Add tests for new functionality
   - Update documentation as needed

3. **Run quality checks**
   ```bash
   # Linting
   flake8 django_concurrent_test tests/
   black --check django_concurrent_test tests/
   isort --check-only django_concurrent_test tests/
   
   # Type checking
   mypy django_concurrent_test/
   
   # Security checks
   bandit -r django_concurrent_test/
   safety check
   
   # Tests
   pytest tests/ -v --cov=django_concurrent_test
   pytest --concurrent tests/
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: add new feature description"
   ```

5. **Push and create a pull request**
   ```bash
   git push origin feature/your-feature-name
   ```

### Commit Message Format

Use conventional commit format:
- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation changes
- `test:` for test additions or changes
- `refactor:` for code refactoring
- `perf:` for performance improvements
- `chore:` for maintenance tasks

## 🐛 Reporting Issues

When reporting issues, please include:

1. **Environment Information**
   - Python version
   - Django version
   - Database type and version
   - Operating system

2. **Reproduction Steps**
   - Clear steps to reproduce the issue
   - Sample code or configuration
   - Expected vs actual behavior

3. **Error Messages**
   - Full error traceback
   - Log output (if applicable)

4. **Additional Context**
   - Any relevant configuration
   - Workarounds you've tried
   - Related issues or discussions

## 🎯 Feature Requests

When requesting features, please:

1. **Describe the Problem**
   - What problem are you trying to solve?
   - Why is this feature needed?

2. **Propose a Solution**
   - How should the feature work?
   - What would the API look like?

3. **Consider Alternatives**
   - Are there existing solutions?
   - Could this be implemented differently?

## 📚 Documentation

### Adding Documentation

- **README.md**: Update for new features or major changes
- **CHANGELOG.md**: Add entries for all user-facing changes
- **Docstrings**: Include comprehensive docstrings for all public APIs
- **Examples**: Provide usage examples for new features

### Documentation Standards

- Use clear, concise language
- Include code examples
- Provide step-by-step instructions
- Update all related documentation

## 🔒 Security

### Security Issues

If you discover a security vulnerability, please:

1. **Do not open a public issue**
2. **Email directly**: ranaehtashamali1@gmail.com
3. **Include details**: Description, reproduction steps, potential impact

### Security Guidelines

- Never commit sensitive information (passwords, API keys, etc.)
- Validate all inputs and file paths
- Use secure defaults for all configurations
- Follow the principle of least privilege

## 🏷️ Release Process

### Versioning

We follow [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

### Release Checklist

- [ ] All tests pass
- [ ] Documentation is updated
- [ ] CHANGELOG.md is updated
- [ ] Version is bumped in pyproject.toml
- [ ] Security review completed
- [ ] Performance testing completed

## 🤝 Community Guidelines

### Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Help others learn and grow
- Follow the project's coding standards

### Communication

- Use clear, professional language
- Be patient with newcomers
- Provide context for suggestions
- Acknowledge contributions

## 📞 Getting Help

If you need help with contributing:

1. **Check existing issues** for similar questions
2. **Read the documentation** thoroughly
3. **Ask in discussions** for general questions
4. **Email directly** for urgent or sensitive matters

## 🙏 Acknowledgments

Thank you to all contributors who have helped make django-concurrent-test better! Your contributions are greatly appreciated.

---

**Happy contributing! 🎉** 