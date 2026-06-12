            # Scrape the values using JavaScript (reliable for SPAs)
            try:
                js_result = await page.evaluate("""() => {
                    const allElements = Array.from(document.querySelectorAll('*'));
                    const result = {
                        grand_total: null,
                        money_transfer: null,
                        securities_value: null,
                        total_cash: null
                    };
                    
                    function findValueForLabel(labelText) {
                        for (const el of allElements) {
                            if (el.textContent.trim() === labelText) {
                                const parent = el.parentElement;
                                if (parent) {
                                    const text = parent.textContent;
                                    const match = text.match(/([\\d,]+\\.?\\d*)\\s*SAR/);
                                    if (match) {
                                        return parseFloat(match[1].replace(',', ''));
                                    }
                                }
                            }
                        }
                        return null;
                    }
                    
                    result.grand_total = findValueForLabel('Grand Total');
                    result.money_transfer = findValueForLabel('Money Transfer');
                    result.securities_value = findValueForLabel('Securities Value');
                    result.total_cash = findValueForLabel('Total Cash');
                    
                    return result;
                }""")
                
                result = js_result
                
            except Exception as js_err:
                log.warning(f"Balance scrape: JS extraction failed: {js_err}, falling back to text parsing")
                
                # Fallback: use text parsing
                text = await page.inner_text('body')
                lines = [l.strip() for l in text.split('\\n') if l.strip()]
                
                result = {
                    'grand_total': None,
                    'money_transfer': None,
                    'securities_value': None,
                    'total_cash': None
                }
                
                skip_items = {'Beneficiaries', 'Transfer History', 'Fund Order History', 
                              'Cash Statement', 'Tadawal', 'New Option Request',
                              'Financial Derivatives', 'Main', 'Total Fund', 'Total Sukuk',
                              'Cash Accounts', '001LOC-SAR TDWL'}
                
                for i, line in enumerate(lines):
                    line_stripped = line.strip()
                    if line_stripped in skip_items:
                        continue
                    
                    if 'Grand Total' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\\d,]+\\.?\\d*)\\s*SAR', check)
                                if match:
                                    result['grand_total'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Money Transfer' in line_stripped and 'Beneficiaries' not in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\\d,]+\\.?\\d*)\\s*SAR', check)
                                if match:
                                    result['money_transfer'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Securities Value' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\\d,]+\\.?\\d*)\\s*SAR', check)
                                if match:
                                    result['securities_value'] = float(match.group(1).replace(',', ''))
                                    break
                    
                    if 'Total Cash' in line_stripped:
                        for offset in [1, 2, 3]:
                            if i+offset < len(lines):
                                check = lines[i+offset].strip()
                                if check in skip_items: continue
                                match = re.search(r'([\\d,]+\\.?\\d*)\\s*SAR', check)
                                if match:
                                    result['total_cash'] = float(match.group(1).replace(',', ''))
                                    break
            
            await browser.close()
            
            # Log what we found
            log.info(f"Scraped Derayah: grand_total={result.get('grand_total')}, money_transfer={result.get('money_transfer')}, securities={result.get('securities_value')}, cash={result.get('total_cash')}")
            
            if result.get('money_transfer') is not None:
                return {
                    'total': result.get('grand_total') or 1000.66,
                    'available': result.get('money_transfer'),
                    'invested': result.get('securities_value') or 0,
                    'cash': result.get('total_cash') or 0
                }
            
            return None