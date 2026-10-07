package org.example.ticket.venue.repository;

import org.example.ticket.venue.domain.VenueHallSeatTemplate;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface VenueHallSeatTemplateRepository extends JpaRepository<VenueHallSeatTemplate, Long> {
    List<VenueHallSeatTemplate> findAllByVenueHallIdOrderByRowNumberAscSeatNumberAsc(Long venueHallId);
}
