from leaktopus.services.enhancement_module.enhancement_module_provider import EnhancementModuleProviderInterface
from leaktopus.services.enhancement_module.enhancement_module_service import EnhancementModuleException
from leaktopus.utils.common_imports import logger
from subprocess import Popen, CalledProcessError, PIPE
import hashlib
from leaktopus.common.contributors import add_contributor, get_contributors

class EnhancementModuleContributorsProvider(EnhancementModuleProviderInterface):
    def __init__(self, override_methods={}, **kwargs):
        self.override_methods = override_methods

    def get_provider_name(self):
        return "contributors"

    def is_contributor_org_domain(self, author_email, committer_email, organization_domains):
        contributors_domains = set()
        author_email_sep = author_email.split('@')
        committer_email_sep = committer_email.split('@')
        if len(author_email_sep) > 1:
            contributors_domains.add(author_email_sep[1])

        if len(committer_email_sep) > 1:
            contributors_domains.add(committer_email_sep[1])

        for domain in contributors_domains:
            if domain in organization_domains:
                return True

        return False

    def get_contributor_checksum(self, contributor):
        contributor_str = contributor['name'] + \
                          contributor['author_email'] + \
                          contributor['committer_email']
        return hashlib.md5(contributor_str.encode('utf-8')).hexdigest()

    def get_existing_contributors_checksums(self, leak):
        existing_contributors_checksums = []
        existing_contributors = get_contributors(leak_id=leak.leak_id)

        for contributor in existing_contributors:
            existing_contributors_checksums.append(self.get_contributor_checksum(contributor))

        return existing_contributors_checksums

    def parse_contributors_results(self, leak_service, url, output, organization_domains):
        contributors = []

        for row in output.splitlines():
            row_parts = row.split("###")
            name = row_parts[0]
            committer_email = row_parts[1]
            author_email = row_parts[2]

            contributors.append({
                "name": name,
                "author_email": author_email,
                "committer_email": committer_email,
                "is_organization_domain": self.is_contributor_org_domain(author_email, committer_email, organization_domains)
            })

        uniq_contributors = [dict(s) for s in set(frozenset(d.items()) for d in contributors)]
        # Get leak id from DB.
        leaks = leak_service.get_leaks(url=url)

        # Exit in case that the leak wasn't found.
        if not leaks:
            raise EnhancementModuleException(f"Cannot find leak for {url}")

        # Calc existing contributors checksums to decide whether new should be inserted to DB.
        existing_contributors_checksums = self.get_existing_contributors_checksums(leaks[0])

        for contributor in uniq_contributors:
            contributor_checksum = self.get_contributor_checksum(contributor)

            # Add the contributor to the DB if not already exists.
            if contributor_checksum not in existing_contributors_checksums:
                add_contributor(
                    leaks[0].leak_id,
                    contributor['name'],
                    contributor['author_email'],
                    contributor['committer_email'],
                    contributor['is_organization_domain']
                )

    def guard_empty_organization_domains(self, potential_leak_source_request):
        if len(potential_leak_source_request.organization_domains) == 0:
            logger.info("No organization domains provided for enhancement module service")
            raise EnhancementModuleException("No organization domains provided for enhancement module service")

    def execute(self, potential_leak_source_request, leak_service, url, full_diff_dir):
        logger.info("Enhancement module contributors is enhancing PLS {} stored in {}", url, full_diff_dir)

        # @todo Get the list of committers from GitHub without cloning the repo.
        # https://api.github.com/repos/:owner/:repo/commits
        try:
            self.guard_empty_organization_domains(potential_leak_source_request)

            # Using ### as a separator between the values.
            git_log_proc = Popen([
                'git',
                'log',
                '--pretty=format:%an###%ce###%aE'
            ], stdout=PIPE, cwd=full_diff_dir)
            # Sort and leave only unique lines for faster processing.
            proc2 = Popen(['sort', '-u'], stdin=git_log_proc.stdout, stdout=PIPE, stderr=PIPE)
            git_log_proc.stdout.close()  # Allow proc1 to receive a SIGPIPE if proc2 exits.
            output, err = proc2.communicate()
            str_output = output.decode()

            if str_output:
                self.parse_contributors_results(leak_service, url, str_output, potential_leak_source_request.organization_domains)
        except EnhancementModuleException:
            return False
        except CalledProcessError as e:
            logger.error("Error while extracting contributors for {} - {}", url, e)
            return False

        logger.debug("Done extracting contributors from {}", url)
