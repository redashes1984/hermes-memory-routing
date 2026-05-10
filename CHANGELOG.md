# Changelog

All notable changes to the Hermes Memory Routing System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-05-10

### Added
- Initial release of Memory Routing System
- Intelligent keyword-based memory routing
- Comprehensive audit trail system
- Automated maintenance tasks (keyword tuning & memory replay)
- Fact cache management system
- Sub-document organization (7 specialized documents)
- Performance metrics and monitoring
- Security features (Base64 encryption, 600 permissions)

### Features
- **Multi-path Routing**: Fast path (score≥3), LLM review (1-2), and fallback (0)
- **Keyword Optimization**: Automatic keyword tuning every 30 minutes
- **Memory Replay**: Idle memory replay every 2 hours
- **Comprehensive Auditing**: Full audit trail with scoring and tracking
- **Security**: Base64 encryption and strict file permissions (600)
- **Performance Metrics**: Real-time performance tracking

### Documentation
- Complete documentation in `docs/` directory
- Architecture diagrams and flowcharts
- Configuration guides
- Monitoring and troubleshooting procedures

## [0.1.0] - 2026-05-09

### Added
- Basic memory routing framework
- Initial keyword matching system
- Audit trail infrastructure

### Changed
- Refactored routing logic for better performance
- Improved error handling and logging

## [0.0.1] - 2026-05-08

### Added
- Initial project structure
- Basic memory management classes
- Keyword configuration system

[Unreleased]: https://github.com/redashes1984/hermes-memory-routing/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/redashes1984/hermes-memory-routing/releases/tag/v1.0.0
[0.1.0]: https://github.com/redashes1984/hermes-memory-routing/releases/tag/v0.1.0
[0.0.1]: https://github.com/redashes1984/hermes-memory-routing/releases/tag/v0.0.1
