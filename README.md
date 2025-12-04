# HGT Bookkeeper

Professional double-entry bookkeeping tools for self-employed individuals and small businesses.

## Overview

**hgt-bookkeeper** automates the conversion of payment processor data into properly formatted, accrual-basis accounting transactions for GnuCash and other double-entry accounting systems.

Built as part of the [Hunt Gather Trade](https://huntgathertrade.substack.com) methodology, this tool handles the complexity of:

- **Accrual-basis accounting** - Record revenue when earned, expenses when incurred
- **Automatic tax withholding** - Calculate and reserve FICA, federal, and state taxes
- **Multi-split transactions** - Proper double-entry format with detailed splits
- **Payment processor fees** - Separate revenue from processing costs
- **S-corp ready** - Structured for easy transition from sole proprietor to S-corporation

## Quick Start

```bash
# Install
pip install hgt-bookkeeper

# Configure accounts
cp config.example.toml config.toml
# Edit config.toml with your account names

# Import Stripe data
hgt-bookkeeper import stripe balance_history.csv

# Import the generated file into GnuCash
```

## Documentation

**[📚 Full Documentation](https://yourusername.github.io/hgt-bookkeeper)**

- [Getting Started Guide](https://yourusername.github.io/hgt-bookkeeper/getting-started)
- [Understanding Accrual Accounting](https://yourusername.github.io/hgt-bookkeeper/tutorials/understanding-accrual)
- [Configuration Reference](https://yourusername.github.io/hgt-bookkeeper/configuration)
- [The HGT Philosophy](https://yourusername.github.io/hgt-bookkeeper/philosophy)

## Features

### Currently Supported

- ✅ Stripe balance history import with complete transaction detail
- ✅ Automatic tax calculation and withholding (FICA, Federal, State)
- ✅ Accrual-basis revenue and liability tracking
- ✅ GnuCash multi-split CSV format
- ✅ Payment processing fee separation

### Roadmap

- 🔄 QuickBooks import format
- 🔄 Bank transaction imports

## Philosophy

This tool embodies the HGT approach to self-reliance: **understand your tools, own your data, build proper systems from the start.**

Rather than relying on cloud accounting software or hoping QuickBooks gets it right, this tool gives you:

- **Transparency** - See exactly how every transaction is recorded
- **Control** - Customize account mappings to match your structure
- **Education** - Learn proper accounting principles through tooling
- **Scalability** - Start as sole proprietor, grow to S-corp without changing systems

## Requirements

- Python 3.8+
- GnuCash (or any double-entry accounting system that imports CSV)

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Author

Created by Larry Kann as part of Hunt Gather Trade LLC's methodology for systematic household and business management.

- **Substack:** [huntgathertrade.substack.com](https://huntgathertrade.substack.com)
- **Email:** larry@huntgathertrade.com

## Contributing

Contributions welcome! This tool is designed to be extended for additional payment processors, export formats, and accounting systems.

See the [documentation](https://yourusername.github.io/hgt-bookkeeper) for development setup and architecture details.
