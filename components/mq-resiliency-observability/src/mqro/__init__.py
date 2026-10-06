"""mq-resiliency-observability: stdlib-only, non-MQI HA/DR state collectors.

Each collector probes the cluster's own CLIs (crm_mon, drbdsetup, dspmq, rdqmstatus,
df/ls) and renders a node_exporter textfile. The installed version comes from
``importlib.metadata`` (see ``mqro.selfcheck``), never from a module attribute.
"""
