from django.shortcuts import get_object_or_404, render, redirect
from django.http.response import Http404, JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import *
from .forms import *
from heltour.tournament.templatetags.tournament_extras import leagueurl
import itertools
from django.db.models.query import Prefetch
from collections import defaultdict
from decorators import cached_as
import re
from django.views.generic import View
from django.core.mail.message import EmailMessage
import json

common_team_models = [League, Season, Round, Team]
common_lone_models = [League, Season, Round, LonePlayerScore, LonePlayerPairing, PlayerPairing, PlayerBye, SeasonPlayer,
                      Player, SeasonPrize, SeasonPrizeWinner]

class BaseView(View):
    def get(self, request, *args, **kwargs):
        self.read_context()
        return self.view(*self.args, **self.kwargs)

    def post(self, request, *args, **kwargs):
        if not hasattr(self, 'view_post'):
            return super(BaseView, self).post(request, *args, **kwargs)
        self.read_context()
        return self.view_post(*self.args, **self.kwargs)

    def read_context(self):
        pass

    def render(self, template, context):
        return render(self.request, template, context)

class LeagueView(BaseView):
    def read_context(self):
        league_tag = self.kwargs.pop('league_tag')
        season_tag = self.kwargs.pop('season_tag', None)
        self.league = _get_league(league_tag)
        self.season = _get_season(league_tag, season_tag, True)

    def render(self, template, context):
        context.update({
            'league': self.league,
            'season': self.season,
            'nav_tree': _get_nav_tree(self.league.tag, self.season.tag if self.season is not None else None),
            'other_leagues': League.objects.order_by('display_order').exclude(pk=self.league.pk)
        })
        return render(self.request, template, context)

class SeasonView(LeagueView):
    def get(self, request, *args, **kwargs):
        self.read_context()
        if not self._season_specified:
            return redirect('by_league:by_season:%s' % request.resolver_match.url_name, *self.args, league_tag=self.league.tag, season_tag=self.season.tag, **self.kwargs)
        return self.view(*self.args, **self.kwargs)

    def read_context(self):
        league_tag = self.kwargs.pop('league_tag')
        season_tag = self.kwargs.pop('season_tag', None)
        self.league = _get_league(league_tag)
        self.season = _get_season(league_tag, season_tag, False)
        self._season_specified = season_tag is not None

class UrlAuthMixin:
    def persist_url_auth(self, secret_token):
        if secret_token is not None:
            auth = PrivateUrlAuth.objects.filter(secret_token=secret_token).first()
            if auth is not None and not auth.is_expired() and not auth.used:
                self.request.session['url_auth_token'] = secret_token
                auth.used = True
                auth.save()
            return True
        return False

    def get_authenticated_user(self):
        username = None
        player = None

        secret_token = self.request.session.get('url_auth_token', '')
        auth = PrivateUrlAuth.objects.filter(secret_token=secret_token).first()
        if auth is not None and not auth.is_expired():
            username = auth.authenticated_user
            player = Player.objects.filter(lichess_username__iexact=username).first()
        # Clean up the DB
        for expired_auth in PrivateUrlAuth.objects.filter(expires__lt=timezone.now()):
            expired_auth.delete()
        return username, player

class HomeView(BaseView):
    def view(self):
        leagues = League.objects.filter(is_active=True).order_by('display_order')

        context = {
            'leagues': leagues,
        }
        return self.render('tournament/home.html', context)

class LeagueHomeView(LeagueView):
    def view(self):
        if self.league.competitor_type == 'team':
            return self.team_view()
        else:
            return self.lone_view()

    def team_view(self):
        other_leagues = League.objects.filter(is_active=True).exclude(pk=self.league.pk).order_by('display_order')

        rules_doc = LeagueDocument.objects.filter(league=self.league, type='rules').first()
        rules_doc_tag = rules_doc.tag if rules_doc is not None else None
        intro_doc = LeagueDocument.objects.filter(league=self.league, type='intro').first()

        if self.season is None:
            context = {
                'rules_doc_tag': rules_doc_tag,
                'intro_doc': intro_doc,
                'can_edit_document': self.request.user.has_perm('tournament.change_document'),
                'other_leagues': other_leagues,
            }
            return self.render('tournament/team_league_home.html', context)

        season_list = Season.objects.filter(league=self.league, is_active=True).order_by('-start_date', '-id').exclude(pk=self.season.pk)
        registration_season = Season.objects.filter(league=self.league, registration_open=True).order_by('-start_date').first()

        team_scores = list(enumerate(sorted(TeamScore.objects.filter(team__season=self.season), reverse=True)[:5], 1))

        context = {
            'team_scores': team_scores,
            'season_list': season_list,
            'rules_doc_tag': rules_doc_tag,
            'intro_doc': intro_doc,
            'can_edit_document': self.request.user.has_perm('tournament.change_document'),
            'registration_season': registration_season,
            'other_leagues': other_leagues,
        }
        return self.render('tournament/team_league_home.html', context)

    def lone_view(self):
        other_leagues = League.objects.filter(is_active=True).exclude(pk=self.league.pk).order_by('display_order')

        rules_doc = LeagueDocument.objects.filter(league=self.league, type='rules').first()
        rules_doc_tag = rules_doc.tag if rules_doc is not None else None
        intro_doc = LeagueDocument.objects.filter(league=self.league, type='intro').first()

        if self.season is None:
            context = {
                'rules_doc_tag': rules_doc_tag,
                'intro_doc': intro_doc,
                'can_edit_document': self.request.user.has_perm('tournament.change_document'),
                'other_leagues': other_leagues,
            }
            return self.render('tournament/lone_league_home.html', context)

        season_list = Season.objects.filter(league=self.league, is_active=True).order_by('-start_date', '-id').exclude(pk=self.season.pk)
        registration_season = Season.objects.filter(league=self.league, registration_open=True).order_by('-start_date').first()

        player_scores = _lone_player_scores(self.season, final=True)[:5]

        if self.season.is_completed:
            prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season=self.season)
            player_highlights = _get_player_highlights(prize_winners)
        else:
            player_highlights = []

        context = {
            'player_scores': player_scores,
            'season_list': season_list,
            'rules_doc_tag': rules_doc_tag,
            'intro_doc': intro_doc,
            'can_edit_document': self.request.user.has_perm('tournament.change_document'),
            'registration_season': registration_season,
            'other_leagues': other_leagues,
            'player_highlights': player_highlights,
        }
        return self.render('tournament/lone_league_home.html', context)

class SeasonLandingView(SeasonView):
    def view(self):
        if self.league.competitor_type == 'team':
            return self.team_view()
        else:
            return self.lone_view()

    def team_view(self):
        @cached_as(SeasonDocument, Document, TeamScore, TeamPairing, *common_team_models)
        def _view(league_tag, season_tag, is_staff):
            if self.season.is_completed:
                return self.team_completed_season_view()

            default_season, season_list = self.get_season_list()

            active_round = Round.objects.filter(season=self.season, publish_pairings=True, is_completed=False, start_date__lt=timezone.now(), end_date__gt=timezone.now()) \
                                        .order_by('-number') \
                                        .first()
            last_round = Round.objects.filter(season=self.season, is_completed=True).order_by('-number').first()
            last_round_pairings = last_round.teampairing_set.all() if last_round is not None else None
            team_scores = list(enumerate(sorted(TeamScore.objects.filter(team__season=self.season), reverse=True)[:5], 1))

            links_doc = SeasonDocument.objects.filter(season=self.season, type='links').first()

            context = {
                'default_season': default_season,
                'season_list': season_list,
                'active_round': active_round,
                'last_round': last_round,
                'last_round_pairings': last_round_pairings,
                'team_scores': team_scores,
                'links_doc': links_doc,
                'can_edit_document': self.request.user.has_perm('tournament.change_document'),
            }
            return self.render('tournament/team_season_landing.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

    def lone_view(self):
        @cached_as(SeasonDocument, Document, *common_lone_models)
        def _view(league_tag, season_tag, is_staff):
            if self.season.is_completed:
                return self.lone_completed_season_view()

            default_season, season_list = self.get_season_list()

            active_round = Round.objects.filter(season=self.season, publish_pairings=True, is_completed=False, start_date__lt=timezone.now(), end_date__gt=timezone.now()) \
                                        .order_by('-number') \
                                        .first()
            last_round = Round.objects.filter(season=self.season, is_completed=True).order_by('-number').first()
            last_round_pairings = last_round.loneplayerpairing_set.exclude(result='').order_by('pairing_order')[:10].nocache() if last_round is not None else None
            player_scores = _lone_player_scores(self.season, final=True)[:5]

            links_doc = SeasonDocument.objects.filter(season=self.season, type='links').first()

            context = {
                'default_season': default_season,
                'season_list': season_list,
                'active_round': active_round,
                'last_round': last_round,
                'last_round_pairings': last_round_pairings,
                'player_scores': player_scores,
                'links_doc': links_doc,
                'can_edit_document': self.request.user.has_perm('tournament.change_document'),
            }
            return self.render('tournament/lone_season_landing.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

    def team_completed_season_view(self):
        default_season, season_list = self.get_season_list()

        round_numbers = list(range(1, self.season.rounds + 1))
        team_scores = list(enumerate(sorted(TeamScore.objects.filter(team__season=self.season).select_related('team').nocache(), reverse=True), 1))

        first_team = team_scores[0][1] if len(team_scores) > 0 else None
        second_team = team_scores[1][1] if len(team_scores) > 1 else None
        third_team = team_scores[2][1] if len(team_scores) > 2 else None

        links_doc = SeasonDocument.objects.filter(season=self.season, type='links').first()

        context = {
            'default_season': default_season,
            'season_list': season_list,
            'round_numbers': round_numbers,
            'team_scores': team_scores,
            'first_team': first_team,
            'second_team': second_team,
            'third_team': third_team,
            'links_doc': links_doc,
            'can_edit_document': self.request.user.has_perm('tournament.change_document'),
        }
        return self.render('tournament/team_completed_season_landing.html', context)

    def lone_completed_season_view(self):
        default_season, season_list = self.get_season_list()

        round_numbers = list(range(1, self.season.rounds + 1))
        player_scores = _lone_player_scores(self.season)

        first_player = player_scores[0][1] if len(player_scores) > 0 else None
        second_player = player_scores[1][1] if len(player_scores) > 1 else None
        third_player = player_scores[2][1] if len(player_scores) > 2 else None

        u1600_winner = SeasonPrizeWinner.objects.filter(season_prize__season=self.season, season_prize__max_rating=1600, season_prize__rank=1).first()
        if u1600_winner is not None:
            u1600_player = find([ps[1] for ps in player_scores], season_player__player=u1600_winner.player)
        else:
            u1600_player = None

        prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season=self.season)
        player_highlights = _get_player_highlights(prize_winners)

        links_doc = SeasonDocument.objects.filter(season=self.season, type='links').first()

        context = {
            'default_season': default_season,
            'season_list': season_list,
            'round_numbers': round_numbers,
            'player_scores': player_scores,
            'first_player': first_player,
            'second_player': second_player,
            'third_player': third_player,
            'u1600_player': u1600_player,
            'player_highlights': player_highlights,
            'links_doc': links_doc,
            'can_edit_document': self.request.user.has_perm('tournament.change_document'),
        }
        return self.render('tournament/lone_completed_season_landing.html', context)

    def get_season_list(self):
        default_season = _get_default_season(self.league.tag, allow_none=True)
        season_list = Season.objects.filter(league=self.league, is_active=True).order_by('-start_date', '-id')
        if default_season:
            season_list = season_list.exclude(pk=default_season.pk)
        return default_season, season_list

class PairingsView(SeasonView):
    def view(self, round_number=None, team_number=None):
        if self.league.competitor_type == 'team':
            return self.team_view(round_number, team_number)
        else:
            return self.lone_view(round_number, team_number)

    def team_view(self, round_number=None, team_number=None):
        @cached_as(TeamScore, TeamPairing, TeamMember, SeasonPlayer, AlternateAssignment, Player, PlayerAvailability, TeamPlayerPairing,
                   PlayerPairing, *common_team_models)
        def _view(league_tag, season_tag, round_number, team_number, is_staff, can_change_pairing):
            specified_round = round_number is not None
            round_number_list = [round_.number for round_ in Round.objects.filter(season=self.season, publish_pairings=True).order_by('-number')]
            if round_number is None:
                try:
                    round_number = round_number_list[0]
                except IndexError:
                    pass
            team_list = self.season.team_set.order_by('name')
            team_pairings = TeamPairing.objects.filter(round__number=round_number, round__season=self.season) \
                                               .order_by('pairing_order') \
                                               .select_related('white_team', 'black_team') \
                                               .nocache()
            if team_number is not None:
                current_team = get_object_or_404(team_list, number=team_number)
                team_pairings = team_pairings.filter(white_team=current_team) | team_pairings.filter(black_team=current_team)
            else:
                current_team = None
            pairing_lists = [list(
                                  team_pairing.teamplayerpairing_set.order_by('board_number')
                                              .select_related('white', 'black')
                                              .nocache()
                            ) for team_pairing in team_pairings]
            unavailable_players = {pa.player for pa in PlayerAvailability.objects.filter(round__season=self.season, round__number=round_number, is_available=False) \
                                                                                 .select_related('player')
                                                                                 .nocache()}
            context = {
                'round_number': round_number,
                'round_number_list': round_number_list,
                'current_team': current_team,
                'team_list': team_list,
                'pairing_lists': pairing_lists,
                'unavailable_players': unavailable_players,
                'specified_round': specified_round,
                'specified_team': team_number is not None,
                'can_edit': can_change_pairing
            }
            return self.render('tournament/team_pairings.html', context)
        return _view(self.league.tag, self.season.tag, round_number, team_number, self.request.user.is_staff, self.request.user.has_perm('tournament.change_pairing'))

    def lone_view(self, round_number=None, team_number=None):
        specified_round = round_number is not None
        round_number_list = [round_.number for round_ in Round.objects.filter(season=self.season, publish_pairings=True).order_by('-number')]
        if round_number is None:
            try:
                round_number = round_number_list[0]
            except IndexError:
                pass
        round_ = Round.objects.filter(number=round_number, season=self.season).first()
        pairings = LonePlayerPairing.objects.filter(round=round_).order_by('pairing_order').select_related('white', 'black').nocache()
        byes = PlayerBye.objects.filter(round=round_).order_by('type', 'player_rank', 'player__lichess_username').select_related('player').nocache()

        next_pairing_order = 0
        for p in pairings:
            next_pairing_order = max(next_pairing_order, p.pairing_order + 1)

        # Find duplicate players
        player_refcounts = {}
        for p in pairings:
            player_refcounts[p.white] = player_refcounts.get(p.white, 0) + 1
            player_refcounts[p.black] = player_refcounts.get(p.black, 0) + 1
        for b in byes:
            player_refcounts[b.player] = player_refcounts.get(b.player, 0) + 1
        duplicate_players = {k for k, v in player_refcounts.items() if v > 1}

        active_players = {sp.player for sp in SeasonPlayer.objects.filter(season=self.season, is_active=True)}

        def pairing_error(pairing):
            if not self.request.user.is_staff:
                return None
            if pairing.white == None or pairing.black == None:
                return 'Missing player'
            if pairing.white in duplicate_players:
                return 'Duplicate player: %s' % pairing.white.lichess_username
            if pairing.black in duplicate_players:
                return 'Duplicate player: %s' % pairing.black.lichess_username
            if not round_.is_completed and pairing.white not in active_players:
                return 'Inactive player: %s' % pairing.white.lichess_username
            if not round_.is_completed and pairing.black not in active_players:
                return 'Inactive player: %s' % pairing.black.lichess_username
            return None

        def bye_error(bye):
            if not self.request.user.is_staff:
                return None
            if bye.player in duplicate_players:
                return 'Duplicate player: %s' % bye.player.lichess_username
            if not round_.is_completed and bye.player not in active_players:
                return 'Inactive player: %s' % bye.player.lichess_username
            return None

        # Add errors
        pairings = [(p, pairing_error(p)) for p in pairings]
        byes = [(b, bye_error(b)) for b in byes]

        context = {
            'round_': round_,
            'round_number_list': round_number_list,
            'pairings': pairings,
            'byes': byes,
            'specified_round': specified_round,
            'next_pairing_order': next_pairing_order,
            'duplicate_players': duplicate_players,
            'can_edit': self.request.user.has_perm('tournament.change_pairing')
        }
        return self.render('tournament/lone_pairings.html', context)

class RegisterView(LeagueView):
    def view(self, post=False):
        if self.season is not None and self.season.registration_open:
            reg_season = self.season
        else:
            reg_season = Season.objects.filter(league=self.league, registration_open=True).order_by('-start_date').first()
        if reg_season is None:
            return self.render('tournament/registration_closed.html', {})

        if post:
            form = RegistrationForm(self.request.POST, season=reg_season)
            if form.is_valid():
                form.save()
                return redirect(leagueurl('registration_success', league_tag=self.league.tag, season_tag=self.season.tag))
        else:
            form = RegistrationForm(season=reg_season)

        context = {
            'form': form,
            'registration_season': reg_season
        }
        return self.render('tournament/register.html', context)

    def view_post(self):
        return self.view(post=True)

class RegistrationSuccessView(SeasonView):
    def view(self):
        if self.season is not None and self.season.registration_open:
            reg_season = self.season
        else:
            reg_season = Season.objects.filter(league=self.league, registration_open=True).order_by('-start_date').first()
        if reg_season is None:
            return self.render('tournament/registration_closed.html', {})

        context = {
            'registration_season': reg_season
        }
        return self.render('tournament/registration_success.html', context)

class RostersView(SeasonView):
    def view(self):
        @cached_as(TeamMember, SeasonPlayer, Alternate, AlternateAssignment, AlternateBucket, Player, PlayerAvailability, *common_team_models,
                        vary_request=lambda r: (r.user.is_staff, r.user.has_perm('tournament.manage_players')))
        def _view(league_tag, season_tag, is_staff):
            if self.league.competitor_type != 'team':
                raise Http404
            if self.season is None:
                context = {
                    'can_edit': self.request.user.has_perm('tournament.manage_players'),
                }
                return self.render('tournament/team_rosters.html', context)

            teams = Team.objects.filter(season=self.season).order_by('number').prefetch_related(
                Prefetch('teammember_set', queryset=TeamMember.objects.select_related('player'))
            ).nocache()
            board_numbers = list(range(1, self.season.boards + 1))

            alternates = Alternate.objects.filter(season_player__season=self.season)
            alternates_by_board = [sorted(
                                          alternates.filter(board_number=n)
                                                    .select_related('season_player__registration', 'season_player__player')
                                                    .nocache(),
                                          key=lambda alt: alt.priority_date()
                                   ) for n in board_numbers]
            alternate_rows = list(enumerate(itertools.izip_longest(*alternates_by_board), 1))
            if len(alternate_rows) == 0:
                alternate_rows.append((1, [None for _ in board_numbers]))

            current_round = Round.objects.filter(season=self.season, publish_pairings=True).order_by('-number').first()
            scheduled_alternates = {assign.player for assign in AlternateAssignment.objects.filter(round=current_round)
                                                                                           .select_related('player')
                                                                                           .nocache()}
            unresponsive_players = {sp.player for sp in SeasonPlayer.objects.filter(season=self.season, unresponsive=True)
                                                                            .select_related('player')
                                                                            .nocache()}
            games_missed_by_player = {sp.player: sp.games_missed for sp in SeasonPlayer.objects.filter(season=self.season)
                                                                                               .select_related('player')
                                                                                               .nocache()}
            yellow_card_players = {player for player, games_missed in games_missed_by_player.items() if games_missed == 1}
            red_card_players = {player for player, games_missed in games_missed_by_player.items() if games_missed >= 2}

            context = {
                'teams': teams,
                'board_numbers': board_numbers,
                'alternate_rows': alternate_rows,
                'scheduled_alternates': scheduled_alternates,
                'unresponsive_players': unresponsive_players,
                'yellow_card_players': yellow_card_players,
                'red_card_players': red_card_players,
                'can_edit': self.request.user.has_perm('tournament.manage_players'),
            }
            return self.render('tournament/team_rosters.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

class StandingsView(SeasonView):
    def view(self, section=None):
        if self.league.competitor_type == 'team':
            return self.team_view()
        else:
            return self.lone_view(section)

    def team_view(self):
        @cached_as(TeamScore, TeamPairing, *common_team_models)
        def _view(league_tag, season_tag, is_staff):
            round_numbers = list(range(1, self.season.rounds + 1))
            team_scores = list(enumerate(sorted(TeamScore.objects.filter(team__season=self.season).select_related('team').nocache(), reverse=True), 1))
            context = {
                'round_numbers': round_numbers,
                'team_scores': team_scores,
            }
            return self.render('tournament/team_standings.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

    def lone_view(self, section=None):
        @cached_as(*common_lone_models)
        def _view(league_tag, season_tag, is_staff):
            round_numbers = list(range(1, self.season.rounds + 1))
            player_scores = _lone_player_scores(self.season)

            if section is not None:
                match = re.match(r'u(\d+)', section)
                if match is not None:
                    max_rating = int(match.group(1))
                    player_scores = [ps for ps in player_scores if ps[1].season_player.seed_rating < max_rating]

            player_sections = [('u%d' % sp.max_rating, 'U%d' % sp.max_rating) for sp in SeasonPrize.objects.filter(season=self.season).exclude(max_rating=None).order_by('max_rating')]
            section_dict = {k: (k, v) for k, v in player_sections}
            current_section = section_dict.get(section, None)

            if self.season.is_completed:
                prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season=self.season)
            else:
                prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season__league=self.league)
            player_highlights = _get_player_highlights(prize_winners)

            context = {
                'round_numbers': round_numbers,
                'player_scores': player_scores,
                'player_sections': player_sections,
                'current_section': current_section,
                'player_highlights': player_highlights,
            }
            return self.render('tournament/lone_standings.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

def _get_player_highlights(prize_winners):
    return [
        ('gold', {pw.player for pw in prize_winners.filter(season_prize__rank=1, season_prize__max_rating=None)}),
        ('silver', {pw.player for pw in prize_winners.filter(season_prize__rank=2, season_prize__max_rating=None)}),
        ('bronze', {pw.player for pw in prize_winners.filter(season_prize__rank=3, season_prize__max_rating=None)}),
        ('blue', {pw.player for pw in prize_winners.filter(season_prize__rank=1).exclude(season_prize__max_rating=None)})
    ]

@cached_as(LonePlayerScore, LonePlayerPairing, PlayerPairing, PlayerBye, SeasonPlayer, Player)
def _lone_player_scores(season, final=False, sort_by_seed=False, include_current=False):
    # For efficiency, rather than having LonePlayerScore.round_scores() do independent
    # calculations, we populate a few common data structures and use those as parameters.

    if sort_by_seed:
        sort_key = lambda s: s.season_player.seed_rating
    elif season.is_completed or final:
        sort_key = lambda s: s.final_standings_sort_key()
    else:
        sort_key = lambda s: s.pairing_sort_key()
    player_scores = list(enumerate(sorted(LonePlayerScore.objects.filter(season_player__season=season).select_related('season_player__player').nocache(), key=sort_key, reverse=True), 1))
    player_number_dict = {p.season_player.player: n for n, p in player_scores}

    pairings = LonePlayerPairing.objects.filter(round__season=season).select_related('white', 'black').nocache()
    white_pairings_dict = defaultdict(list)
    black_pairings_dict = defaultdict(list)
    for p in pairings:
        if p.white is not None:
            white_pairings_dict[p.white].append(p)
        if p.black is not None:
            black_pairings_dict[p.black].append(p)

    byes = PlayerBye.objects.filter(round__season=season).select_related('round', 'player').nocache()
    byes_dict = defaultdict(list)
    for bye in byes:
        byes_dict[bye.player].append(bye)

    rounds = Round.objects.filter(season=season).order_by('number')
    # rounds = [round_ for round_ in Round.objects.filter(season=season).order_by('number') if round_.is_completed or (include_current and round_.publish_pairings)]

    def round_scores(player_score):
        return list(player_score.round_scores(rounds, player_number_dict, white_pairings_dict, black_pairings_dict, byes_dict, include_current))

    return [(n, ps, round_scores(ps)) for n, ps in player_scores]

class CrosstableView(SeasonView):
    def view(self):
        @cached_as(TeamScore, TeamPairing, *common_team_models)
        def _view(league_tag, season_tag, is_staff):
            if self.league.competitor_type != 'team':
                raise Http404
            team_scores = TeamScore.objects.filter(team__season=self.season).order_by('team__number').select_related('team').nocache()
            context = {
                'team_scores': team_scores,
            }
            return self.render('tournament/team_crosstable.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

class WallchartView(SeasonView):
    def view(self):
        @cached_as(*common_lone_models)
        def _view(league_tag, season_tag, is_staff):
            if self.league.competitor_type == 'team':
                raise Http404
            round_numbers = list(range(1, self.season.rounds + 1))
            player_scores = _lone_player_scores(self.season, sort_by_seed=True, include_current=True)

            if self.season.is_completed:
                prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season=self.season)
            else:
                prize_winners = SeasonPrizeWinner.objects.filter(season_prize__season__league=self.season.league)
            player_highlights = _get_player_highlights(prize_winners)

            context = {
                'round_numbers': round_numbers,
                'player_scores': player_scores,
                'player_highlights': player_highlights,
            }
            return self.render('tournament/lone_wallchart.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

class ResultView(SeasonView):
    def view(self, pairing_id):
        team_pairing = get_object_or_404(TeamPairing, round__season=self.season, pk=pairing_id)
        pairings = team_pairing.teamplayerpairing_set.order_by('board_number').nocache()
        context = {
            'team_pairing': team_pairing,
            'pairings': pairings,
            'round_number': team_pairing.round.number,
        }
        return self.render('tournament/team_match_result.html', context)

class StatsView(SeasonView):
    def view(self):
        @cached_as(League, Season, Round, TeamPlayerPairing, PlayerPairing)
        def _view(league_tag, season_tag, is_staff):
            if self.league.competitor_type != 'team':
                raise Http404

            all_pairings = PlayerPairing.objects.filter(teamplayerpairing__team_pairing__round__season=self.season) \
                                                .select_related('teamplayerpairing', 'white', 'black') \
                                                .nocache()

            def count_results(board_num=None):
                total = 0.0
                counts = [0, 0, 0, 0]
                rating_delta = 0
                for p in all_pairings:
                    if board_num is not None and p.teamplayerpairing.board_number != board_num:
                        continue
                    if p.game_link == '' or p.result == '':
                        # Don't count forfeits etc
                        continue
                    total += 1
                    if p.white.rating is not None and p.black.rating is not None:
                        rating_delta += p.white.rating - p.black.rating
                    if p.result == '1-0':
                        counts[0] += 1
                        counts[3] += 1
                    elif p.result == '0-1':
                        counts[2] += 1
                        counts[3] -= 1
                    elif p.result == '1/2-1/2':
                        counts[1] += 1
                if total == 0:
                    return board_num, tuple(counts), (0, 0, 0, 0), 0.0
                percents = (counts[0] / total, counts[1] / total, counts[2] / total, counts[3] / total)
                return board_num, tuple(counts), percents, rating_delta / total

            _, total_counts, total_percents, total_rating_delta = count_results()
            boards = [count_results(board_num=n) for n in self.season.board_number_list()]

            context = {
                'has_win_rate_stats': total_counts != (0, 0, 0, 0),
                'total_rating_delta': total_rating_delta,
                'total_counts': total_counts,
                'total_percents': total_percents,
                'boards': boards,
            }
            return self.render('tournament/team_stats.html', context)
        return _view(self.league.tag, self.season.tag, self.request.user.is_staff)

class LeagueDashboardView(LeagueView):
    def view(self):
        if self.league.competitor_type == 'team':
            return self.team_view()
        else:
            return self.lone_view()

    def team_view(self):
        default_season = _get_default_season(self.league.tag, allow_none=True)
        season_list = list(Season.objects.filter(league=self.league).order_by('-start_date', '-id'))
        if default_season is not None:
            season_list.remove(default_season)

        pending_reg_count = len(Registration.objects.filter(season=self.season, status='pending'))

        team_members = TeamMember.objects.filter(team__season=self.season).select_related('player').nocache()
        alternates = Alternate.objects.filter(season_player__season=self.season).select_related('season_player__player').nocache()
        season_players = set(sp.player for sp in SeasonPlayer.objects.filter(season=self.season, is_active=True).select_related('player').nocache())
        team_players = set(tm.player for tm in team_members)
        alternate_players = set(alt.season_player.player for alt in alternates)
        unassigned_player_count = len(season_players - team_players - alternate_players)

        last_round = Round.objects.filter(season=self.season, publish_pairings=True, is_completed=False).order_by('number').first()
        next_round = Round.objects.filter(season=self.season, publish_pairings=False, is_completed=False).order_by('number').first()

        context = {
            'default_season': default_season,
            'season_list': season_list,
            'pending_reg_count': pending_reg_count,
            'unassigned_player_count': unassigned_player_count,
            'last_round': last_round,
            'next_round': next_round
        }
        return self.render('tournament/team_league_dashboard.html', context)

    def lone_view(self):
        default_season = _get_default_season(self.league.tag, allow_none=True)
        season_list = list(Season.objects.filter(league=self.league).order_by('-start_date', '-id'))
        if default_season is not None:
            season_list.remove(default_season)

        pending_reg_count = len(Registration.objects.filter(season=self.season, status='pending'))

        team_members = TeamMember.objects.filter(team__season=self.season).select_related('player').nocache()
        alternates = Alternate.objects.filter(season_player__season=self.season).select_related('season_player__player').nocache()
        season_players = set(sp.player for sp in SeasonPlayer.objects.filter(season=self.season, is_active=True).select_related('player').nocache())
        team_players = set(tm.player for tm in team_members)
        alternate_players = set(alt.season_player.player for alt in alternates)
        unassigned_player_count = len(season_players - team_players - alternate_players)

        last_round = Round.objects.filter(season=self.season, publish_pairings=True, is_completed=False).order_by('number').first()
        next_round = Round.objects.filter(season=self.season, publish_pairings=False, is_completed=False).order_by('number').first()

        context = {
            'default_season': default_season,
            'season_list': season_list,
            'pending_reg_count': pending_reg_count,
            'unassigned_player_count': unassigned_player_count,
            'last_round': last_round,
            'next_round': next_round
        }
        return self.render('tournament/lone_league_dashboard.html', context)

class DocumentView(LeagueView):
    def view(self, document_tag):
        league_document = LeagueDocument.objects.filter(league=self.league, tag=document_tag).first()
        if league_document is None:
            season_document = SeasonDocument.objects.filter(season=self.season, tag=document_tag).first()
            if season_document is None:
                raise Http404
            document = season_document.document
        else:
            document = league_document.document

        context = {
            'document': document,
            'is_faq': False,
            'can_edit': self.request.user.has_perm('tournament.change_document'),
        }
        return self.render('tournament/document.html', context)

class ContactView(LeagueView):
    def view(self, post=False):
        leagues = [self.league] + list(League.objects.order_by('display_order').exclude(pk=self.league.pk))
        if post:
            form = ContactForm(self.request.POST, leagues=leagues)
            if form.is_valid():
                league = League.objects.get(tag=form.cleaned_data['league'])
                for mod in league.leaguemoderator_set.all():
                    if mod.send_contact_emails and mod.player.email:
                        message = EmailMessage(
                            '[%s] %s' % (league.name, form.cleaned_data['subject']),
                            'Sender:\n%s\n%s\n\nMessage:\n%s' %
                                (form.cleaned_data['your_lichess_username'], form.cleaned_data['your_email_address'], form.cleaned_data['message']),
                            settings.DEFAULT_FROM_EMAIL,
                            [mod.player.email]
                        )
                        message.send()
                return redirect(leagueurl('contact_success', league_tag=self.league.tag))
        else:
            form = ContactForm(leagues=leagues)

        context = {
            'form': form,
        }
        return self.render('tournament/contact.html', context)

    def view_post(self):
        return self.view(post=True)

class ContactSuccessView(LeagueView):
    def view(self):
        context = {
        }
        return self.render('tournament/contact_success.html', context)

class PlayerProfileView(LeagueView):
    def view(self, username):
        player = get_object_or_404(Player, lichess_username__iexact=username)

        def game_count(season):
            if season.league.competitor_type == 'team':
                season_pairings = TeamPlayerPairing.objects.filter(team_pairing__round__season=season)
            else:
                season_pairings = LonePlayerPairing.objects.filter(round__season=season)
            return (season_pairings.filter(white=player) | season_pairings.filter(black=player)).count()

        def team(season):
            if season.league.competitor_type == 'team':
                team_member = player.teammember_set.filter(team__season=season).first()
                if team_member is not None:
                    return team_member.team
            return None

        other_season_leagues = [(l, [(sp.season, game_count(sp.season), team(sp.season)) for sp in player.seasonplayer_set \
                                                                                                         .filter(season__league=l, season__is_active=True) \
                                                                                                         .exclude(season=self.season) \
                                                                                                         .order_by('-season__start_date')]) \
                         for l in League.objects.order_by('display_order')]
        other_season_leagues = [l for l in other_season_leagues if len(l[1]) > 0]

        season_player = SeasonPlayer.objects.filter(season=self.season, player=player).first()

        if self.season is None:
            games = None
        elif self.season.league.competitor_type == 'team':
            pairings = TeamPlayerPairing.objects.filter(white=player) | TeamPlayerPairing.objects.filter(black=player)
            games = [(p.team_pairing.round, p, p.white_team() if p.white == player else p.black_team()) for p in pairings.filter(team_pairing__round__season=self.season).exclude(result='').order_by('team_pairing__round__number').nocache()]
        else:
            pairings = LonePlayerPairing.objects.filter(white=player) | LonePlayerPairing.objects.filter(black=player)
            games = [(p.round, p, None) for p in pairings.filter(round__season=self.season).exclude(result='').order_by('round__number').nocache()]

        team_member = TeamMember.objects.filter(team__season=self.season, player=player).first()
        alternate = Alternate.objects.filter(season_player=season_player).first()

        schedule = []
        for round_ in self.season.round_set.filter(is_completed=False).order_by('number'):
            if self.season.league.competitor_type == 'team':
                pairing = pairings.filter(team_pairing__round=round_).first()
            else:
                pairing = pairings.filter(round=round_).first()
            if pairing is not None:
                if pairing.result != '':
                    continue
                schedule.append((round_, pairing, None, None))
                continue
            if self.season.league.competitor_type == 'team':
                assignment = AlternateAssignment.objects.filter(round=round_, player=player).first()
                if assignment is not None and (team_member is None or team_member.team != assignment.team):
                    schedule.append((round_, None, 'Scheduled', assignment.team))
                    continue
                if season_player is None or not season_player.is_active:
                    continue
                availability = PlayerAvailability.objects.filter(round=round_, player=player).first()
                if availability is not None and not availability.is_available:
                    schedule.append((round_, None, 'Unavailable', None))
                    continue
                if team_member is not None:
                    schedule.append((round_, None, 'Scheduled', None))
                    continue
                schedule.append((round_, None, 'Available', None))
            else:
                bye = PlayerBye.objects.filter(round=round_, player=player).first()
                if bye is not None:
                    schedule.append((round_, None, bye.get_type_display(), None))
                    continue
                if season_player is None or not season_player.is_active:
                    continue
                schedule.append((round_, None, 'Scheduled', None))

        context = {
            'player': player,
            'other_season_leagues': other_season_leagues,
            'season_player': season_player,
            'games': games,
            'team_member': team_member,
            'alternate': alternate,
            'schedule': schedule,
        }
        return self.render('tournament/player_profile.html', context)

class TeamProfileView(LeagueView):
    def view(self, team_number):
        team = get_object_or_404(Team, season=self.season, number=team_number)

        member_players = {tm.player for tm in team.teammember_set.all()}
        game_counts = defaultdict(int)
        for tp in team.pairings_as_white.all():
            for p in tp.teamplayerpairing_set.nocache():
                if p.board_number % 2 == 1:
                    if p.white is not None:
                        game_counts[p.white] += 1
                else:
                    if p.black is not None:
                        game_counts[p.black] += 1
        for tp in team.pairings_as_black.all():
            for p in tp.teamplayerpairing_set.nocache():
                if p.board_number % 2 == 1:
                    if p.black is not None:
                        game_counts[p.black] += 1
                else:
                    if p.white is not None:
                        game_counts[p.white] += 1

        prev_members = [(player, game_count) for player, game_count in sorted(game_counts.items(), key=lambda i: i[0].lichess_username.lower()) if player not in member_players]

        matches = []
        for round_ in self.season.round_set.filter(publish_pairings=True).order_by('number'):
            if self.season.league.competitor_type == 'team':
                pairing = (team.pairings_as_white.all() | team.pairings_as_black.all()).filter(round=round_).first()
            if pairing is not None:
                matches.append((round_, pairing))

        context = {
            'team': team,
            'prev_members': prev_members,
            'matches': matches,
        }
        return self.render('tournament/team_profile.html', context)

class NominateView(SeasonView, UrlAuthMixin):
    def view(self, secret_token=None, post=False):
        can_nominate = False
        current_nominations = []
        form = None

        if self.persist_url_auth(secret_token):
            return redirect('by_league:nominate', self.league.tag)
        username, player = self.get_authenticated_user()

        if self.league.competitor_type == 'team':
            season_pairings = PlayerPairing.objects.filter(teamplayerpairing__team_pairing__round__season=self.season).nocache()
        else:
            season_pairings = PlayerPairing.objects.filter(loneplayerpairing__round__season=self.season).nocache()

        if player is not None:
            player_pairings = season_pairings.filter(white=player) | season_pairings.filter(black=player)
            can_nominate = player_pairings.count() > 0

            if can_nominate and self.season.nominations_open:
                current_nominations = GameNomination.objects.filter(season=self.season, nominating_player=player)

                if post:
                    form = NominateForm(self.request.POST)
                    if form.is_valid():
                        with transaction.atomic():
                            if form.cleaned_data['game_link'] != '':
                                pairing = season_pairings.filter(game_link=form.cleaned_data['game_link']).first()
                                if pairing is not None:
                                    for nom in current_nominations:
                                        nom.delete()
                                    nom = GameNomination.objects.create(season=self.season, nominating_player=player, game_link=form.cleaned_data['game_link'], pairing=pairing)
                                    current_nominations = [nom]
                                else:
                                    form.add_error('game_link', ValidationError('The game link doesn\'t match any pairings this season.', code='invalid'))
                            else:
                                for nom in current_nominations:
                                    nom.delete()
                                current_nominations = []
                else:
                    form = NominateForm()
                if len(current_nominations) > 0:
                    form.fields['game_link'].initial = current_nominations[0].game_link

        # Clean up the DB
        for expired_auth in PrivateUrlAuth.objects.filter(expires__lt=timezone.now()):
            expired_auth.delete()

        context = {
            'form': form,
            'username': username,
            'can_nominate': can_nominate,
            'current_nominations': current_nominations,
        }
        return self.render('tournament/nominate.html', context)

    def view_post(self):
        return self.view(post=True)

class ScheduleView(LeagueView, UrlAuthMixin):
    def view(self, secret_token=None, post=False):
        if self.persist_url_auth(secret_token):
            return redirect('by_league:edit_schedule', self.league.tag)
        username, player = self.get_authenticated_user()

        times = player.availabletime_set.filter(league=self.league) if player is not None else None

        context = {
            'username': username,
            'player': player,
            'times': times,
        }
        return self.render('tournament/schedule.html', context)

    def view_post(self):
        return self.view(post=True)

class TvView(LeagueView):
    def view(self):
        leagues = list(League.objects.order_by('display_order'))
        if self.season.is_active and not self.season.is_completed:
            active_season = self.season
        else:
            active_season = _get_default_season(self.league.tag, True)

        boards = active_season.board_number_list() if active_season is not None and active_season.boards is not None else None
        teams = active_season.team_set.order_by('name') if active_season is not None else None

        filter_form = TvFilterForm(current_league=self.league, leagues=leagues, boards=boards, teams=teams)
        timezone_form = TvTimezoneForm()

        context = {
            'filter_form': filter_form,
            'timezone_form': timezone_form,
            'json': json.dumps(_tv_json(self.league)),
        }
        return self.render('tournament/tv.html', context)

class TvJsonView(LeagueView):
    def view(self):
        league_tag = self.request.GET.get('league')
        if league_tag == 'all':
            league = None
        elif league_tag is not None:
            league = League.objects.filter(tag=league_tag).first()
        else:
            league = self.league
        try:
            board = int(self.request.GET.get('board', ''))
        except ValueError:
            board = None
        try:
            team = int(self.request.GET.get('team', ''))
        except ValueError:
            team = None
        return JsonResponse(_tv_json(league, board, team))

def _tv_json(league, board=None, team=None):
    def export_game(game, league, board, team):
        if hasattr(game, 'teamplayerpairing'):
            return {
                'id': game.game_id(),
                'white': str(game.white),
                'white_name': game.white.lichess_username,
                'black': str(game.black),
                'black_name': game.black.lichess_username,
                'time': game.scheduled_time.isoformat() if game.scheduled_time is not None else None,
                'league': game.teamplayerpairing.team_pairing.round.season.league.tag,
                'season': game.teamplayerpairing.team_pairing.round.season.tag,
                'white_team': {
                    'name': game.teamplayerpairing.white_team_name(),
                    'number': game.teamplayerpairing.white_team().number,
                },
                'black_team': {
                    'name': game.teamplayerpairing.black_team_name(),
                    'number': game.teamplayerpairing.black_team().number,
                },
                'board_number': game.teamplayerpairing.board_number,
                'matches_filter': (league is None or league == game.teamplayerpairing.team_pairing.round.season.league) and
                                  (board is None or board == game.teamplayerpairing.board_number) and
                                  # TODO: Team filter can do weird things if there are multiple active seasons
                                  (team is None or team == game.teamplayerpairing.white_team().number or team == game.teamplayerpairing.black_team().number)
            }
        elif hasattr(game, 'loneplayerpairing'):
            return {
                'id': game.game_id(),
                'white': str(game.white),
                'black': str(game.black),
                'time': game.scheduled_time.isoformat() if game.scheduled_time is not None else None,
                'league': game.loneplayerpairing.round.season.league.tag,
                'season': game.loneplayerpairing.round.season.tag,
                'matches_filter': (league is None or league == game.loneplayerpairing.round.season.league) and board is None and team is None
            }
    current_games = PlayerPairing.objects.filter(result='', tv_state='default').exclude(game_link='').order_by('scheduled_time') \
                                         .select_related('teamplayerpairing__team_pairing__round__season__league',
                                                         'teamplayerpairing__team_pairing__black_team',
                                                         'teamplayerpairing__team_pairing__white_team',
                                                         'loneplayerpairing__round__season__league').nocache()
    scheduled_games = PlayerPairing.objects.filter(result='', game_link='', scheduled_time__gt=timezone.now() - timedelta(minutes=20)).order_by('scheduled_time') \
                                         .select_related('teamplayerpairing__team_pairing__round__season__league',
                                                         'teamplayerpairing__team_pairing__black_team',
                                                         'teamplayerpairing__team_pairing__white_team',
                                                         'loneplayerpairing__round__season__league').nocache()
    return {'games': [export_game(g, league, board, team) for g in current_games],
            'schedule': [export_game(g, league, board, team) for g in scheduled_games]}

def _get_league(league_tag, allow_none=False):
    if league_tag is None:
        return _get_default_league(allow_none)
    else:
        return get_object_or_404(League, tag=league_tag)

def _get_default_league(allow_none=False):
    try:
        return League.objects.filter(is_default=True).order_by('id')[0]
    except IndexError:
        league = League.objects.order_by('id').first()
        if not allow_none and league is None:
            raise Http404
        return league

def _get_season(league_tag, season_tag, allow_none=False):
    if season_tag is None:
        return _get_default_season(league_tag, allow_none)
    else:
        return get_object_or_404(Season, league=_get_league(league_tag), tag=season_tag)

def _get_default_season(league_tag, allow_none=False):
    season = Season.objects.filter(league=_get_league(league_tag), is_active=True).order_by('-start_date', '-id').first()
    if not allow_none and season is None:
        raise Http404
    return season

@cached_as(NavItem)
def _get_nav_tree(league_tag, season_tag):
    league = _get_league(league_tag)
    root_items = league.navitem_set.filter(parent=None).order_by('order')

    def transform(item):
        text = item.text
        url = item.path
        if item.season_relative and season_tag is not None:
            url = '/season/%s' % season_tag + url
        if item.league_relative:
            url = '/%s' % league_tag + url
        children = [transform(child) for child in item.navitem_set.order_by('order')]
        append_separator = item.append_separator
        return (text, url, children, append_separator)

    return [transform(item) for item in root_items]

