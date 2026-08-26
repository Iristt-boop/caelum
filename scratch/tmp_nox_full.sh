#!/bin/bash
journalctl -u nox-core --since "20 min ago" --no-pager | tail -30
