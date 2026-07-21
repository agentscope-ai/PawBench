from playwright.sync_api import sync_playwright
import time


def test_registration_form():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto('file:///app/working/workspaces/default/form.html')  # Adjust the path if needed

        # Step 1: Personal Info
        page.fill('[data-testid=fullname]', 'John Doe')
        page.fill('[data-testid=email]', 'john.doe@example.com')
        page.fill('[data-testid=phone]', '+1 555-123-4567')

        def retry_on_failure(action, max_attempts=3, delay=0.5):
            for attempt in range(max_attempts):
                try:
                    action()
                    return True
                except:
                    if attempt < max_attempts - 1:  # i.e. not the last attempt
                        time.sleep(delay)
                    else:
                        raise

        # Navigate to step 2
        retry_on_failure(lambda: page.click('[data-testid=next-1]'))

        # Validate UI state after step 1
        assert page.is_visible('[data-testid=step-2]')
        assert page.is_hidden('[data-testid=step-1]')
        assert page.locator('[data-testid=progress-bar] [data-testid=prog-1]').get_attribute('class').contains('active')
        assert page.locator('[data-testid=progress-bar] [data-testid=prog-2]').get_attribute('class').contains('active')

        # Step 2: Address Details
        page.fill('[data-testid=street]', '123 Main St')
        page.fill('[data-testid=city]', 'San Francisco')
        page.select_option('[data-testid=state]', 'CA')
        page.fill('[data-testid=zip]', '94102')

        # Navigate to step 3
        retry_on_failure(lambda: page.click('[data-testid=next-2]'))

        # Validate UI state after step 2
        assert page.is_visible('[data-testid=step-3]')
        assert page.is_hidden('[data-testid=step-2]')
        assert page.locator('[data-testid=progress-bar] [data-testid=prog-3]').get_attribute('class').contains('active')

        # Step 3: Review & Submit
        review_fullname = page.text_content('[data-testid=review-fullname]')
        review_email = page.text_content('[data-testid=review-email]')
        review_phone = page.text_content('[data-testid=review-phone]')
        review_address = page.text_content('[data-testid=review-address]')

        assert review_fullname == 'John Doe'
        assert review_email == 'john.doe@example.com'
        assert review_phone == '+1 555-123-4567'
        assert review_address == '123 Main St, San Francisco, CA 94102'

        # Submit the form
        retry_on_failure(lambda: page.click('[data-testid=submit]'))

        # Validate success panel and submission ID
        assert page.is_visible('[data-testid=success-panel]')
        submission_id = page.text_content('[data-testid=submission-id]')
        assert submission_id.startswith('REG-') and len(submission_id) == 10

        # Save a screenshot of the final success state
        page.screenshot(path='success.png')

        browser.close()

if __name__ == '__main__':
    test_registration_form()