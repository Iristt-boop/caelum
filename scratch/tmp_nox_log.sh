#!/bin/bash
journalctl -u nox-core --since "30 min ago" --no-pager | grep -iE "location|在哪|加载|context" | tail -20
