"""Deliver each fiction draft before its auxiliary event-summary work."""


def fiction_order(plan):
    if plan.get('workflow')!='fiction': return plan
    nodes={s['id']:s for s in plan['steps']}; seen=set(); ordered=[]
    def visit(sid):
        if sid in seen:return
        node=nodes[sid]
        for dep in node['dependencies']+node.get('length_dependencies',[]): visit(dep)
        seen.add(sid); ordered.append(node)
    # Chapter gates force prior chapter memory only when the next chapter needs it.
    for step in plan['steps']:
        if step['kind']=='approval':
            visit(step['id'])
            for candidate in plan['steps']:
                if candidate['kind']=='approved_copy' and step['id'] in candidate['dependencies']:visit(candidate['id'])
    for step in plan['steps']: visit(step['id'])
    return {**plan,'steps':ordered}
