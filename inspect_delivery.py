content = open('app.py', encoding='utf-8').read()
idx = content.find('def admin_schedule_delivery')
print('=== admin_schedule_delivery ===')
print(content[idx:idx+900])

content2 = open('templates/admin_requests.html', encoding='utf-8').read()
idx2 = content2.find('Schedule Delivery')
print('\n=== delivery form in template ===')
print(content2[idx2:idx2+1400])
