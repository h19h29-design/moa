"""One private input versus current formal fields in its exact original scope.

Read one SQLite snapshot. Never align block IDs across receiving/conversion
versions, rebase inputs, apply migrations, or append drafts/review events.
"""
from . import school_review as review, school_review_drafts as drafts, school_service as svc


def compare(store, principal, ident):
    store.db.execute('SAVEPOINT private_draft_comparison')
    try:
        row=drafts.get(store,principal,ident)
        member=svc.require_member(store,principal)
        version=svc.get_version(store,principal,row['notice_version'])
        latest=store.db.execute('SELECT latest_version FROM school_service_posts WHERE school=? AND id=?',
            (principal.school,version['post_id'])).fetchone()[0]
        head=drafts.metadata(store,principal,row['notice_version'],row['conversion_revision'],[row['target_key']])[row['target_key']]
        reasons=[]
        if head['id']!=ident: reasons.append('newer_input')
        if member['role'] not in svc.WRITE_ROLES: reasons.append('read_only')
        if latest!=row['notice_version']: reasons.append('receiving_changed')
        if version['revision']!=row['conversion_revision']: reasons.append('conversion_changed')
        if version['state']=='processing': reasons.append('conversion_pending')
        if version['state']=='conversion_failed': reasons.append('conversion_failed')
        current={'revision':row['conversion_revision'],'available':False,'review_id':'','sequence':0,'created_at':'','status':'unreviewed'}
        fields, relation, comparable = {}, None, False
        try:
            v=review.view(store,principal,row['notice_version'],revision_id=row['conversion_revision'])
        except review.Conflict:
            reasons.append('structure_changed')
        except LookupError:
            reasons.append('structure_unavailable')
        else:
            current['available']=True
            event=v['latest']
            if event:
                current.update(review_id=event['id'],sequence=event['sequence'],created_at=event['created_at'],status=v['status'])
            if v['migration_required']: reasons.append('migration_required')
            if v['base_hash']!=row['base_hash']: reasons.append('structure_changed')
            else:
                if current['review_id']!=row['base_review']: reasons.append('review_changed')
                analysis=v['analysis']; action=row['payload']['action']
                if action['kind']=='block':
                    block=next((b for b in analysis['blocks'] if b['id']==action['block_id']),None)
                    if block is None: reasons.append('item_missing')
                    else:
                        fields={name:{'current':block[name],'saved':action[saved]}
                                for name,saved in (('kind','block_kind'),('text','text'))}
                        comparable=True
                else:
                    edge=action['relation']
                    count=sum(all(r[k]==value for k,value in edge.items()) for r in analysis['relations'])
                    endpoints={b['id'] for b in analysis['blocks']} | {'file:'+f['id'] for f in analysis['files']}
                    relation={'current_count':count,'saved_present':action['kind']=='relation_add',
                              'source_exists':edge['source'] in endpoints,'target_exists':edge['target'] in endpoints}
                    comparable=True
                    if action['kind']=='relation_remove' and not count: reasons.append('item_missing')
        return {'draft':row,'current':current,'head':head,'latest_version':latest,'reasons':reasons,
                'fields':fields,'relation':relation,'comparable':comparable,'recoverable':not reasons and comparable}
    finally:
        store.db.execute('RELEASE private_draft_comparison')
